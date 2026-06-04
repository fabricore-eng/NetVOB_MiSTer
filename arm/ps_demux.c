/*
 * ps_demux.c — streaming MPEG-2 Program Stream demuxer.
 *
 * See ps_demux.h for the contract. This is a hand-rolled byte-incremental
 * state machine: no full-buffer assumptions, no dynamic allocation, no
 * platform/FPGA headers. It is deliberately tolerant of being fed one byte at
 * a time (ring-buffer / network-recv chunking).
 *
 * Parsing model (ISO/IEC 13818-1):
 *   - Units begin with a 32-bit start code  00 00 01 <stream_id>.
 *   - pack_start_code (0xBA): 14-byte MPEG-2 pack header (10 fixed + 0..7
 *     stuffing bytes; stuffing count = low 3 bits of the 14th... i.e. the byte
 *     at offset 9 after the stream_id). Carries SCR; we skip it.
 *   - system_header (0xBB), program_stream_map (0xBC), private_stream_2/nav
 *     (0xBF), padding (0xBE): 16-bit length then that many payload bytes, all
 *     skipped (nav + padding explicitly dropped per spec).
 *   - PES (video 0xE0-0xEF, MPEG audio 0xC0-0xDF, private_1 0xBD): 16-bit
 *     PES_packet_length, then PES header (flags + PTS/DTS + stuffing), then
 *     payload. Video payload -> ES sink; audio payload counted/dropped.
 *   - MPEG_program_end_code (0xB9): no length; just resync to next start code.
 */
#include "ps_demux.h"

#include <string.h>

/* ---- PTS/DTS field decode -------------------------------------------------
 *
 * A PTS (or DTS) is a 33-bit value spread across 5 bytes with marker bits:
 *
 *   byte0:  0010 PTS[32..30] 1         (top nibble 0x2 for PTS-only, 0x3 with
 *                                        DTS present; we ignore the prefix and
 *                                        only mask out the data + marker bits)
 *   byte1:  PTS[29..22]
 *   byte2:  PTS[21..15] 1
 *   byte3:  PTS[14..7]
 *   byte4:  PTS[6..0] 1
 */
static uint64_t decode_ts(const uint8_t *p)
{
    uint64_t ts;
    ts  = (uint64_t)((p[0] >> 1) & 0x07) << 30;
    ts |= (uint64_t)(p[1]) << 22;
    ts |= (uint64_t)((p[2] >> 1) & 0x7F) << 15;
    ts |= (uint64_t)(p[3]) << 7;
    ts |= (uint64_t)((p[4] >> 1) & 0x7F);
    return ts;
}

static int sid_is_video(uint8_t sid)
{
    return sid >= PS_SID_VIDEO_BASE && sid <= PS_SID_VIDEO_LAST;
}

static int sid_is_mpeg_audio(uint8_t sid)
{
    return sid >= PS_SID_AUDIO_BASE && sid <= PS_SID_AUDIO_LAST;
}

/* A stream_id that carries a PES header (flags/PTS/DTS) and a payload. */
static int sid_is_media_pes(uint8_t sid)
{
    return sid_is_video(sid) || sid_is_mpeg_audio(sid) ||
           sid == PS_SID_PRIVATE_1;
}

/* Length-delimited section we skip wholesale (system hdr, map, nav, padding). */
static int sid_is_skip_section(uint8_t sid)
{
    return sid == PS_SID_SYSTEM_HEADER || sid == PS_SID_PROGRAM_MAP ||
           sid == PS_SID_PRIVATE_2     || sid == PS_SID_PADDING;
}

void ps_demux_init(ps_demux *d, ps_video_es_sink sink, void *user)
{
    memset(d, 0, sizeof(*d));
    d->state          = PS_ST_START_CODE;
    d->sink           = sink;
    d->sink_user      = user;
    d->last_video_pts = PS_PTS_NONE;
    d->last_video_dts = PS_PTS_NONE;
    d->last_audio_pts = PS_PTS_NONE;
}

const ps_stats *ps_demux_stats(const ps_demux *d) { return &d->stats; }
uint64_t ps_demux_last_video_pts(const ps_demux *d) { return d->last_video_pts; }
uint64_t ps_demux_last_video_dts(const ps_demux *d) { return d->last_video_dts; }
uint64_t ps_demux_last_audio_pts(const ps_demux *d) { return d->last_audio_pts; }

/* Account a newly-identified stream_id and choose the next state. */
static void begin_unit(ps_demux *d, uint8_t sid)
{
    d->stream_id     = sid;
    d->is_video      = 0;
    d->pkt_unbounded = 0;

    if (sid == PS_SID_PACK) {
        d->stats.pack_headers++;
        /* 10 fixed bytes follow the start code, then the stuffing-length byte
         * is the last of those 10 (offset 9). We count down 10 then read N. */
        d->pack_remaining     = 10;
        d->pack_have_stuffing = 0;
        d->state              = PS_ST_PACK;
        return;
    }

    if (sid == PS_SID_PROGRAM_END) {
        /* No body; just hunt for the next start code. */
        d->state = PS_ST_START_CODE;
        return;
    }

    if (sid_is_skip_section(sid)) {
        if (sid == PS_SID_SYSTEM_HEADER) d->stats.system_headers++;
        else if (sid == PS_SID_PADDING)  d->stats.padding_pes++;
        else if (sid == PS_SID_PRIVATE_2) d->stats.nav_private2++;
        d->len_have      = 0;
        d->skip_len_read = 0;
        d->pkt_remaining = 0;
        d->state         = PS_ST_SKIP; /* read 16-bit length then skip body */
        return;
    }

    if (sid_is_media_pes(sid)) {
        if (sid_is_video(sid)) {
            d->is_video = 1;
            d->stats.video_pes++;
        } else if (sid_is_mpeg_audio(sid)) {
            d->stats.audio_pes_mpeg++;
        } else { /* private_stream_1 */
            d->stats.audio_pes_private1++;
        }
        d->len_have = 0;
        d->state    = PS_ST_PES_LEN;
        return;
    }

    /* Recognized start code we don't specifically model: treat as a
     * length-delimited section so we stay aligned (most such ids carry a
     * 16-bit length per the systems spec). */
    d->stats.other_pes++;
    d->len_have       = 0;
    d->skip_len_read  = 0;
    d->pkt_remaining  = 0;
    d->state          = PS_ST_SKIP; /* SKIP reads the 16-bit length first */
    return;
}

/* Emit a run of video ES payload (or just account it if no sink). */
static void emit_video(ps_demux *d, const uint8_t *p, size_t n)
{
    d->stats.video_es_bytes += n;
    if (d->sink && n) d->sink(p, n, d->sink_user);
}

/*
 * Process the accumulated PES header (flags block already gathered). Parses
 * PTS/DTS and records them; advances to payload streaming.
 *
 * d->pes_hdr holds: [0]=flags1 [1]=flags2 [2]=PES_header_data_length, then the
 * optional-field bytes [3..]. We only decode PTS/DTS; the remaining optional
 * fields + stuffing are accounted by PES_header_data_length and skipped.
 */
static void finish_pes_header(ps_demux *d)
{
    uint8_t  flags2  = d->pes_hdr[1];
    uint8_t  pts_dts = (flags2 >> 6) & 0x3;
    const uint8_t *opt = &d->pes_hdr[3]; /* PTS(5)+DTS(5) always fully captured */
    uint64_t pts = PS_PTS_NONE, dts = PS_PTS_NONE;

    if (pts_dts == 0x2 || pts_dts == 0x3) {
        pts = decode_ts(opt);
        if (pts_dts == 0x3) dts = decode_ts(opt + 5);
    }

    if (d->is_video) {
        if (pts != PS_PTS_NONE) d->last_video_pts = pts;
        if (dts != PS_PTS_NONE) d->last_video_dts = dts;
        else if (pts != PS_PTS_NONE) d->last_video_dts = pts;
    } else {
        if (pts != PS_PTS_NONE) d->last_audio_pts = pts;
    }

    /* By here the full PES header (3 flag bytes + PES_header_data_length
     * optional bytes) has been consumed; pkt_remaining (when bounded) now
     * equals exactly the payload length. */
    if (!d->pkt_unbounded && d->pkt_remaining == 0) {
        d->state = PS_ST_START_CODE; /* degenerate: no payload */
    } else {
        if (d->pkt_unbounded) { d->pend_zeros = 0; d->sc_primed = 0; }
        d->state = PS_ST_PES_PAYLOAD;
    }
}

size_t ps_demux_feed(ps_demux *d, const uint8_t *data, size_t len)
{
    size_t i = 0;

    while (i < len) {
        switch (d->state) {

        case PS_ST_START_CODE: {
            /* Shift bytes in until we see 00 00 01 xx. */
            for (; i < len; i++) {
                d->sc_shift = (d->sc_shift << 8) | data[i];
                if (d->sc_primed < 4) d->sc_primed++;
                if (d->sc_primed >= 4 &&
                    (d->sc_shift & 0xFFFFFF00u) == 0x00000100u) {
                    uint8_t sid = (uint8_t)(d->sc_shift & 0xFF);
                    i++;                 /* consume the stream_id byte */
                    d->sc_primed = 0;    /* require a fresh prefix next time */
                    d->sc_shift  = 0;
                    begin_unit(d, sid);
                    break;
                }
            }
            break;
        }

        case PS_ST_PACK: {
            if (!d->pack_have_stuffing) {
                /* Consume the 10 fixed bytes; the last one carries low-3-bit
                 * stuffing length. */
                while (d->pack_remaining > 0 && i < len) {
                    uint8_t b = data[i++];
                    d->pack_remaining--;
                    if (d->pack_remaining == 0) {
                        /* `b` is the 10th fixed byte: 5 reserved bits + 3
                         * pack_stuffing_length bits. */
                        d->pack_remaining     = (uint32_t)(b & 0x07);
                        d->pack_have_stuffing = 1;
                    }
                }
                if (d->pack_remaining == 0 && d->pack_have_stuffing) {
                    d->state = PS_ST_START_CODE;
                }
            }
            if (d->pack_have_stuffing) {
                while (d->pack_remaining > 0 && i < len) {
                    i++;
                    d->pack_remaining--;
                }
                if (d->pack_remaining == 0) d->state = PS_ST_START_CODE;
            }
            break;
        }

        case PS_ST_PES_LEN: {
            while (d->len_have < 2 && i < len) {
                d->len_buf[d->len_have++] = data[i++];
            }
            if (d->len_have < 2) break;

            uint32_t plen = ((uint32_t)d->len_buf[0] << 8) | d->len_buf[1];

            /* Media PES: a zero packet_length means "unbounded" (allowed for
             * video in a PS) — payload runs to the next start code. */
            if (plen == 0) {
                d->pkt_unbounded = 1;
                d->pkt_remaining = 0;
            } else {
                d->pkt_unbounded = 0;
                d->pkt_remaining = plen;
            }
            /* Next: PES header flags. Collect the 3-byte mandatory block. */
            d->pes_hdr_have  = 0;
            d->pes_hdr_need  = 3;
            d->pes_hdr_stage = 0;
            d->state         = PS_ST_PES_HDR;
            break;
        }

        case PS_ST_PES_HDR: {
            /* Stage 0: read flags1, flags2, PES_header_data_length (3 bytes).
             * Stage 1: read PES_header_data_length optional-field bytes. */
            while (d->pes_hdr_have < d->pes_hdr_need && i < len) {
                uint8_t b = data[i++];
                if (d->pes_hdr_have < sizeof(d->pes_hdr))
                    d->pes_hdr[d->pes_hdr_have] = b;
                d->pes_hdr_have++;
                if (!d->pkt_unbounded && d->pkt_remaining > 0)
                    d->pkt_remaining--;
            }
            if (d->pes_hdr_have < d->pes_hdr_need) break;

            if (d->pes_hdr_stage == 0) {
                /* Validate MPEG-2 PES: top two bits of flags1 are '10'. If not,
                 * this is an MPEG-1-style PES header — handle minimally by
                 * treating all 3 captured bytes as payload-adjacent and not
                 * decoding timestamps (DVD/VOB is MPEG-2, so this is a guard). */
                if ((d->pes_hdr[0] & 0xC0) != 0x80) {
                    /* MPEG-1 path is out of scope for DVD PS; stream the
                     * remainder as payload conservatively (no PTS decode). The 3
                     * bytes were already counted against pkt_remaining. */
                    if (!d->pkt_unbounded && d->pkt_remaining == 0) {
                        d->state = PS_ST_START_CODE;
                    } else {
                        if (d->pkt_unbounded) { d->pend_zeros = 0; d->sc_primed = 0; }
                        d->state = PS_ST_PES_PAYLOAD;
                    }
                    break;
                }
                uint8_t hdr_len = d->pes_hdr[2];
                /* Cap optional-field capture to our scratch space; we only need
                 * the first 10 bytes (PTS+DTS). Anything beyond is skipped, but
                 * still counted against pkt_remaining via the read loop. */
                uint8_t cap = hdr_len;
                if (cap > (uint8_t)(sizeof(d->pes_hdr) - 3))
                    cap = (uint8_t)(sizeof(d->pes_hdr) - 3);
                d->pes_opt_total = hdr_len;
                d->pes_opt_cap   = cap;
                d->pes_opt_tail  = (uint32_t)hdr_len - (uint32_t)cap;
                d->pes_hdr_stage = 1;
                d->pes_hdr_need  = 3u + cap;
                if (d->pes_hdr_have >= d->pes_hdr_need) {
                    /* hdr_len <= cap: no further capture needed this iteration;
                     * fall through to stage-1 completion handling below. */
                } else {
                    break; /* need more bytes for the captured optional block */
                }
            }

            /* Stage 1: captured optional fields are in pes_hdr[3..]. Skip any
             * remaining (uncaptured) optional/stuffing bytes, then proceed. */
            while (d->pes_opt_tail > 0 && i < len) {
                i++;
                d->pes_opt_tail--;
                if (!d->pkt_unbounded && d->pkt_remaining > 0)
                    d->pkt_remaining--;
            }
            if (d->pes_opt_tail > 0) break; /* need more bytes */
            finish_pes_header(d);
            break;
        }

        case PS_ST_PES_PAYLOAD: {
            if (!d->pkt_unbounded) {
                /* Bounded payload: emit exactly pkt_remaining bytes. */
                size_t avail = len - i;
                size_t take  = d->pkt_remaining < avail ? d->pkt_remaining : avail;
                if (d->is_video && take) emit_video(d, &data[i], take);
                i += take;
                d->pkt_remaining -= (uint32_t)take;
                if (d->pkt_remaining == 0) d->state = PS_ST_START_CODE;
                break;
            }

            /* Unbounded payload (PES_packet_length==0): stream bytes until the
             * next start-code prefix 00 00 01. pend_zeros carries a run of
             * trailing 0x00 from a previous feed that may begin a prefix. */
            {
                int matched = 0;
                while (i < len) {
                    uint8_t b = data[i];
                    if (b == 0x01 && d->pend_zeros >= 2) {
                        /* 00 00 01 prefix complete; the two 0x00 + this 0x01 are
                         * structure, not payload (already withheld via pend). */
                        i++;                 /* consume the 0x01 */
                        d->pend_zeros = 0;
                        d->sc_shift   = 0x00000001u; /* prime: 00 00 01 seen */
                        d->sc_primed  = 3;
                        d->state      = PS_ST_START_CODE;
                        matched = 1;
                        break;
                    }
                    if (b == 0x00) {
                        d->pend_zeros++;     /* withhold; could start a prefix */
                        i++;
                        continue;
                    }
                    /* Non-prefix byte: flush any withheld zeros as payload,
                     * then this byte. */
                    if (d->is_video) {
                        for (int z = 0; z < d->pend_zeros; z++) {
                            uint8_t zero = 0x00;
                            emit_video(d, &zero, 1);
                        }
                        emit_video(d, &b, 1);
                    }
                    d->pend_zeros = 0;
                    i++;
                }
                if (matched) break;
                /* Chunk exhausted with no prefix: cap withheld zeros at 2 so the
                 * count stays small across feeds; excess zeros are real payload
                 * and must be emitted now. */
                if (d->pend_zeros > 2) {
                    int extra = d->pend_zeros - 2;
                    if (d->is_video) {
                        for (int z = 0; z < extra; z++) {
                            uint8_t zero = 0x00;
                            emit_video(d, &zero, 1);
                        }
                    }
                    d->pend_zeros = 2;
                }
            }
            break;
        }

        case PS_ST_SKIP: {
            if (!d->skip_len_read) {
                /* Read the 16-bit section/packet length first. */
                while (d->len_have < 2 && i < len) {
                    d->len_buf[d->len_have++] = data[i++];
                }
                if (d->len_have < 2) break;
                d->pkt_remaining =
                    ((uint32_t)d->len_buf[0] << 8) | d->len_buf[1];
                d->skip_len_read = 1;
            }
            {
                size_t avail = len - i;
                size_t skip  = d->pkt_remaining < avail ? d->pkt_remaining : avail;
                i += skip;
                d->pkt_remaining -= (uint32_t)skip;
                if (d->pkt_remaining == 0) d->state = PS_ST_START_CODE;
            }
            break;
        }

        } /* switch */
    } /* while */

    return len;
}
