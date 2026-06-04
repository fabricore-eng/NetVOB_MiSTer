/*
 * feeder.h — host model of the sd_* sector-pull backpressure (transport §4).
 *
 * This models, with NO hardware, the end-to-end pull pacing that the real ARM
 * ingest app implements at the sd_* CD-sector seam:
 *
 *   ARM ring buffer ──(pull fixed-size sectors)──► FPGA decoder input FIFO ──► decode
 *
 * In hardware: the FPGA asserts sd_rd when its input FIFO drains (low-water);
 * the ARM services that request by handing it the next CD-sector worth of video
 * ES (via sd_buff_dout / sd_buff_wr), sourced from the network ring buffer. The
 * decoder only pulls as fast as it drains, so the whole chain is paced by real
 * consumption — there is no free-running producer that can overrun the FIFO.
 *
 * The model here makes that pacing testable off-target:
 *   - a modeled decoder FIFO with a capacity and a low-water mark;
 *   - the FIFO "drains" by a caller-driven amount each tick (modelling the
 *     decoder consuming bits at the video rate);
 *   - the feeder pulls a fixed-size SECTOR of video ES from the ring and pushes
 *     it to the FIFO ONLY while the FIFO is at/below low-water AND a full sector
 *     is available in the ring AND it fits in the FIFO — i.e. it pulls exactly
 *     as the FIFO drains, never overruns the FIFO, and conserves every byte.
 *
 * The sd_* seam itself is abstracted as a plain callback (sector_sink) so this
 * file pulls in NO MiSTer/FPGA headers and is pure C11 (cc/clang). On real
 * hardware that callback is the sd_buff_* write into the core.
 *
 * Reference: docs/transport.md §4 (buffering & flow-control) and §6 (flush on
 * seek — feeder_flush()).
 */
#ifndef NETVOB_ARM_FEEDER_H
#define NETVOB_ARM_FEEDER_H

#include <stddef.h>
#include <stdint.h>

#include "ringbuf.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * The sd_* seam, abstracted. Called once per sector the feeder delivers to the
 * decoder. `data`/`len` is the sector payload (len == feeder.sector_size, valid
 * only for the call). `user` is the opaque value from feeder_init. On hardware
 * this is the sd_buff_dout/sd_buff_wr write; in tests it captures bytes so the
 * test can assert byte conservation and ordering.
 */
typedef void (*sector_sink)(const uint8_t *data, size_t len, void *user);

typedef struct {
    ringbuf    *ring;        /* source of video ES (producer = TCP/demux)      */
    size_t      sector_size; /* fixed sd_* sector size (bytes per pull)        */

    /* Modeled FPGA decoder input FIFO. */
    size_t      fifo_cap;    /* FIFO capacity in bytes                         */
    size_t      fifo_low;    /* FIFO low-water: at/below this, pull a sector   */
    size_t      fifo_level;  /* current FIFO occupancy (bytes)                 */

    sector_sink sink;        /* the sd_* seam callback                         */
    void       *sink_user;

    /* Lifetime accounting (drives tests + runtime health). */
    uint64_t    sectors_pulled;  /* sectors moved ring -> FIFO                 */
    uint64_t    bytes_pulled;    /* == sectors_pulled * sector_size            */
    uint64_t    bytes_drained;   /* total bytes the decoder has consumed       */
    uint64_t    stall_starved;   /* pump iterations blocked: ring < a sector   */
    uint64_t    fifo_overruns;   /* MUST stay 0: a push that wouldn't fit       */
} feeder;

/*
 * Initialise. Requires sector_size>0, fifo_cap>=sector_size (so at least one
 * sector fits). fifo_low is clamped to [0, fifo_cap]. sink may be NULL (bytes
 * still move into the modeled FIFO and are accounted). Returns 0 / -1 on bad
 * args.
 */
int feeder_init(feeder *f, ringbuf *ring, size_t sector_size,
                size_t fifo_cap, size_t fifo_low,
                sector_sink sink, void *user);

/*
 * Pull pacing step (the heart of the model). While the FIFO is at/below its
 * low-water mark, pull one fixed-size sector from the ring and push it to the
 * FIFO — but ONLY if a whole sector is available in the ring and it fits in the
 * FIFO. Stops as soon as any of those stop holding. Returns the number of
 * sectors delivered this call (0 if the FIFO is above low-water, or the ring
 * can't supply a full sector). Never overruns the FIFO; never delivers a
 * partial sector.
 *
 * This is "pull only as the FIFO low-waters" — call it after every drain.
 */
size_t feeder_pump(feeder *f);

/*
 * Model the decoder consuming `bytes` out of the FIFO (clamped to the current
 * level). This is the drain that creates room and re-triggers pulls. Returns
 * the bytes actually drained.
 */
size_t feeder_drain(feeder *f, size_t bytes);

/*
 * Convenience: drain `bytes`, then pump. Returns sectors delivered by the pump.
 * Models one decoder tick (consume, then refill to low-water).
 */
size_t feeder_tick(feeder *f, size_t drain_bytes);

/* Flush the modeled FIFO (e.g. on seek; the ring is flushed separately via
 * ringbuf_clear). FIFO level resets to 0; lifetime counters preserved. */
void feeder_flush(feeder *f);

/* Queries. */
size_t feeder_fifo_level(const feeder *f);
size_t feeder_fifo_free(const feeder *f);
int    feeder_fifo_below_low(const feeder *f); /* level <= fifo_low           */

#ifdef __cplusplus
}
#endif

#endif /* NETVOB_ARM_FEEDER_H */
