/*
 * realpipe.h — shared contract for the real end-to-end ingest pipeline engine.
 *
 * The engine (realpipe.c) runs: file chunks -> ringbuf -> ps_demux -> feeder
 * (sd_* sector pull) on a real (or synthetic) MPEG-2 Program Stream, and
 * reports per-stage stats + the conservation/pacing invariants. Both the
 * developer harness main() (tools/realpipe.c, -DREALPIPE_MAIN) and the committed
 * test (tests/test_realpipe.c) link the same engine and share these types via
 * this header, so the result layout can never drift between them.
 *
 * OFF-TARGET host logic: pure C11, no FPGA/MiSTer headers.
 */
#ifndef NETVOB_ARM_REALPIPE_H
#define NETVOB_ARM_REALPIPE_H

#include <stdint.h>
#include "../ps_demux.h"

#ifdef __cplusplus
extern "C" {
#endif

/* PES-level audio tally from the chunked pass (proves detection + routing). */
typedef struct {
    uint64_t total_bytes;
    uint64_t runs;             /* audio sink callbacks                          */
    uint64_t pes_mpeg_bytes;   /* bytes from 0xC0..0xDF MPEG audio              */
    uint64_t pes_priv1_bytes;  /* bytes from 0xBD private_stream_1              */
    uint8_t  last_sid;
} audio_tally;

/* Accurate private_stream_1 substream-id histogram from a one-shot pass. */
typedef struct {
    uint64_t sub_runs[256];    /* PES whose first payload byte == this id        */
    uint64_t sub_bytes[256];
} substream_hist;

typedef struct {
    int      ok;                       /* 1 if every invariant held              */
    uint64_t file_bytes;               /* bytes read from the input              */
    uint64_t demux_consumed;           /* bytes ps_demux_feed reported consumed  */
    ps_stats stats;                    /* demuxer stats                          */
    uint64_t video_es_produced;        /* demux -> ring                          */
    uint64_t video_es_through_feeder;  /* feeder sector sink bytes (sd_* writes) */
    uint64_t ring_residue;             /* bytes left in ring at EOF              */
    uint64_t fifo_residue;             /* bytes left in modeled FIFO at EOF      */
    uint64_t ring_overruns;            /* ringbuf overrun_events                 */
    uint64_t fifo_overruns;            /* feeder fifo_overruns (MUST be 0)       */
    uint64_t last_video_pts;           /* final decoded video PTS (90 kHz)       */
    uint64_t last_audio_pts;           /* final decoded audio PTS (90 kHz)       */
    audio_tally    audio;
    substream_hist hist;
    char     fail[256];                /* first failed invariant (if !ok)        */
} pipe_result;

/*
 * Run the whole pipeline on `path`, optionally writing the video ES to
 * `out_path` (NULL = count only). Returns 0 if the input opened and the
 * pipeline ran (inspect out->ok for invariant pass/fail), or -1 if the input
 * file could not be opened (callers may SKIP).
 */
int realpipe_run(const char *path, const char *out_path, pipe_result *out);

#ifdef __cplusplus
}
#endif

#endif /* NETVOB_ARM_REALPIPE_H */
