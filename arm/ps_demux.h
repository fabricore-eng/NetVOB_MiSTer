/*
 * ps_demux.h — streaming MPEG-2 Program Stream demuxer (host-testable core).
 *
 * Splits a PS byte stream into:
 *   - video elementary stream (ES) bytes  -> destined for the sd_* seam
 *   - audio elementary stream (ES) bytes  -> destined for an audio sink for the
 *                                            future AC-3/MP2 -> PCM decode path
 *                                            (MPEG audio 0xC0-0xDF, and AC-3/DTS/
 *                                            LPCM via private_stream_1 0xBD).
 *                                            If no audio sink is installed, audio
 *                                            is still recognized/counted as before.
 *
 * It extracts PTS/DTS from PES headers for PTS bookkeeping, and discards
 * non-payload structure: pack headers (0xBA), system headers (0xBB),
 * padding_stream (0xBE) and private_stream_2 / navigation (0xBF).
 *
 * Design: incremental / byte-fed. You may push the stream in arbitrarily small
 * or large chunks (e.g. straight from a network ring buffer); video ES bytes
 * are emitted via a caller-supplied sink callback as they are parsed.
 *
 * This is OFF-TARGET host logic. It pulls in NO FPGA/MiSTer headers and builds
 * with a plain C11 toolchain (cc/clang) on a developer workstation.
 *
 * References: docs/transport.md §1 (container layers), §4 (ring-buffer feed),
 * §5 (PTS-based A/V sync). ISO/IEC 13818-1 (MPEG-2 systems): pack header,
 * PES packet, PTS/DTS field encoding.
 */
#ifndef NETVOB_ARM_PS_DEMUX_H
#define NETVOB_ARM_PS_DEMUX_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* MPEG-2 systems stream_id values we care about. */
#define PS_SID_PROGRAM_END     0xB9 /* MPEG_program_end_code            */
#define PS_SID_PACK            0xBA /* pack_start_code                  */
#define PS_SID_SYSTEM_HEADER   0xBB /* system_header_start_code         */
#define PS_SID_PROGRAM_MAP     0xBC /* program_stream_map               */
#define PS_SID_PRIVATE_1       0xBD /* private_stream_1 (AC-3/DTS/LPCM) */
#define PS_SID_PADDING         0xBE /* padding_stream                   */
#define PS_SID_PRIVATE_2       0xBF /* private_stream_2 (nav PCI/DSI)   */
/* Audio: 110x xxxx (0xC0-0xDF). Video: 1110 xxxx (0xE0-0xEF). */
#define PS_SID_AUDIO_BASE      0xC0
#define PS_SID_AUDIO_LAST      0xDF
#define PS_SID_VIDEO_BASE      0xE0
#define PS_SID_VIDEO_LAST      0xEF

/* Sentinel: "no timestamp seen yet". A real 33-bit PTS is always < 2^33, so
 * this UINT64_MAX value can never collide with a parsed timestamp. */
#define PS_PTS_NONE  UINT64_MAX

/*
 * Sink for emitted video ES bytes. Called with consecutive runs of video
 * elementary-stream payload (PES payload with the PES header stripped). The
 * pointer is into an internal/caller buffer valid only for the call duration;
 * copy if you need to retain it. `user` is the opaque value from ps_demux_init.
 */
typedef void (*ps_video_es_sink)(const uint8_t *data, size_t len, void *user);

/*
 * Sink for emitted audio ES bytes. Called with consecutive runs of audio
 * elementary-stream payload (PES payload with the PES header stripped), tagged
 * with the originating `stream_id` so the consumer can fan out by codec/track:
 *   - 0xC0-0xDF  MPEG audio (MP2/MP1)             -> MP2 decode
 *   - 0xBD       private_stream_1                  -> AC-3 / DTS / LPCM
 * (For private_stream_1, the leading substream-id / frame-count bytes are part
 * of the payload as delivered; the AC-3/MP2 decode stage strips them — this
 * demuxer stays codec-agnostic and routes the raw PES payload byte-exactly.)
 * The pointer is valid only for the call duration; copy to retain. `user` is
 * the opaque value from ps_demux_set_audio_sink.
 */
typedef void (*ps_audio_es_sink)(uint8_t stream_id, const uint8_t *data,
                                 size_t len, void *user);

/* Internal parser phases (exposed only so the struct can be stack-allocated). */
typedef enum {
    PS_ST_START_CODE = 0, /* hunting/aligning to a 00 00 01 xx start code   */
    PS_ST_PACK,           /* consuming a pack header (14 + stuffing bytes)  */
    PS_ST_PES_LEN,        /* reading the 16-bit PES packet_length           */
    PS_ST_PES_HDR,        /* reading PES extension/flags + PTS/DTS + stuff  */
    PS_ST_PES_PAYLOAD,    /* streaming payload (video -> ES, others drop)   */
    PS_ST_SKIP            /* skipping a length-delimited, non-PES section   */
} ps_state;

/* Demuxer statistics — useful for tests and runtime health. */
typedef struct {
    uint64_t pack_headers;       /* 0xBA packs seen                          */
    uint64_t system_headers;     /* 0xBB system headers seen (dropped)       */
    uint64_t video_pes;          /* 0xE0-0xEF PES packets seen               */
    uint64_t audio_pes_mpeg;     /* 0xC0-0xDF MPEG audio PES seen            */
    uint64_t audio_pes_private1; /* 0xBD private_stream_1 (AC-3 etc.) seen   */
    uint64_t padding_pes;        /* 0xBE padding dropped                     */
    uint64_t nav_private2;       /* 0xBF nav private_stream_2 dropped        */
    uint64_t other_pes;          /* recognized but unhandled stream_ids      */
    uint64_t video_es_bytes;     /* total video ES payload bytes emitted     */
    uint64_t audio_es_bytes;     /* total audio ES payload bytes emitted     */
} ps_stats;

/*
 * Streaming demuxer state. Treat as opaque; allocate on the stack or heap and
 * pass to ps_demux_init() before feeding bytes.
 */
typedef struct {
    ps_state state;

    /* Rolling 4-byte start-code shift register (most-recent byte in low 8b). */
    uint32_t sc_shift;
    int      sc_primed;   /* how many bytes have entered sc_shift (caps at 4) */

    /* Current PES/section context. */
    uint8_t  stream_id;       /* stream_id of the unit being parsed          */
    int      is_video;        /* current unit routes payload to the video sink*/
    int      is_audio;        /* current unit routes payload to the audio sink*/
    uint32_t pkt_remaining;   /* bytes left in current length-bounded region */
    int      pkt_unbounded;   /* PES packet_length==0 (video may be unbounded)*/

    /* Pack-header byte countdown (10 fixed bytes, then N stuffing bytes). */
    uint32_t pack_remaining;
    int      pack_have_stuffing; /* parsed the stuffing-count byte yet        */

    /* PES-header scratch (flags + PTS/DTS + PES_header_data_length). */
    uint8_t  pes_hdr[16];     /* flags1+flags2+len + up to 10 PTS/DTS bytes   */
    uint32_t pes_hdr_have;    /* bytes accumulated so far                     */
    uint32_t pes_hdr_need;    /* total bytes to accumulate for this stage     */
    int      pes_hdr_stage;   /* 0=flags(3 bytes) 1=optional-field block      */
    uint8_t  pes_opt_total;   /* PES_header_data_length (full optional len)   */
    uint8_t  pes_opt_cap;     /* optional bytes captured into pes_hdr         */
    uint32_t pes_opt_tail;    /* optional bytes still to skip from the stream */

    /* Two-byte big-endian length accumulator (PES_packet_length / section). */
    uint8_t  len_buf[2];
    uint32_t len_have;
    int      skip_len_read;   /* SKIP state: have we read the 16-bit length?  */

    /* Trailing 0x00 run carried across feeds while scanning an unbounded
     * payload for the next 00 00 01 start-code prefix. */
    int      pend_zeros;

    /* Most-recently parsed timestamps (PS_PTS_NONE if absent). */
    uint64_t last_video_pts;
    uint64_t last_video_dts;
    uint64_t last_audio_pts;

    ps_video_es_sink sink;
    void            *sink_user;

    ps_audio_es_sink audio_sink;
    void            *audio_sink_user;

    ps_stats stats;
} ps_demux;

/* Initialise a demuxer. `sink` may be NULL (video ES then only counted). The
 * audio sink defaults to NULL (audio counted but not routed); install one with
 * ps_demux_set_audio_sink() if you want the audio ES payload. */
void ps_demux_init(ps_demux *d, ps_video_es_sink sink, void *user);

/* Install (or clear, with NULL) the audio ES sink. May be called any time after
 * init; affects audio PES parsed afterwards. */
void ps_demux_set_audio_sink(ps_demux *d, ps_audio_es_sink sink, void *user);

/* Feed `len` bytes. Safe to call repeatedly with any chunking. Returns the
 * number of bytes consumed (always == len; the parser never stalls). */
size_t ps_demux_feed(ps_demux *d, const uint8_t *data, size_t len);

/* Convenience accessors. */
const ps_stats *ps_demux_stats(const ps_demux *d);
uint64_t        ps_demux_last_video_pts(const ps_demux *d);
uint64_t        ps_demux_last_video_dts(const ps_demux *d);
uint64_t        ps_demux_last_audio_pts(const ps_demux *d);

#ifdef __cplusplus
}
#endif

#endif /* NETVOB_ARM_PS_DEMUX_H */
