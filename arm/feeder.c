/*
 * feeder.c — host model of the sd_* sector-pull backpressure.
 *
 * See feeder.h for the contract. The pump implements the exact pacing rule:
 *
 *   while (FIFO at/below low-water):
 *       if (ring has a full sector) and (sector fits in FIFO):
 *           pull one sector from the ring;
 *           push it through the sd_* seam (sink);
 *           FIFO level += sector_size;
 *       else:
 *           stop (starved / would-overrun)        <- backpressure
 *
 * Because the loop's gate is "FIFO at/below low-water", the feeder only pulls
 * as fast as feeder_drain() empties the FIFO. The "fits in FIFO" guard makes a
 * FIFO overrun structurally impossible (we count fifo_overruns and the tests
 * assert it stays 0). Every byte pulled from the ring lands in the FIFO and is
 * later drained, so bytes are conserved across the chain.
 *
 * No dynamic allocation here; the sector scratch is a small stack copy out of
 * the ring (the ring's wrap handling lives in ringbuf.c). For the modeled
 * sizes this is fine; a hardware path would DMA directly.
 */
#include "feeder.h"

#include <string.h>

/* Upper bound on a single modeled sector copy. Real CD sectors are 2048 (or
 * 2324/2336 for mode-2 forms); we cap generously for the host model. A caller
 * asking for a larger sector_size is rejected at init. */
#define FEEDER_MAX_SECTOR 4096u

int feeder_init(feeder *f, ringbuf *ring, size_t sector_size,
                size_t fifo_cap, size_t fifo_low,
                sector_sink sink, void *user)
{
    if (!f || !ring) return -1;
    if (sector_size == 0 || sector_size > FEEDER_MAX_SECTOR) return -1;
    if (fifo_cap < sector_size) return -1;    /* at least one sector must fit */

    memset(f, 0, sizeof(*f));
    f->ring        = ring;
    f->sector_size = sector_size;
    f->fifo_cap    = fifo_cap;
    f->fifo_low    = fifo_low > fifo_cap ? fifo_cap : fifo_low;
    f->fifo_level  = 0;
    f->sink        = sink;
    f->sink_user   = user;
    return 0;
}

size_t feeder_fifo_level(const feeder *f) { return f->fifo_level; }
size_t feeder_fifo_free(const feeder *f)  { return f->fifo_cap - f->fifo_level; }
int    feeder_fifo_below_low(const feeder *f) { return f->fifo_level <= f->fifo_low; }

size_t feeder_pump(feeder *f)
{
    uint8_t sector[FEEDER_MAX_SECTOR];
    size_t  delivered = 0;

    /* Pull only while the decoder FIFO has low-watered. */
    while (f->fifo_level <= f->fifo_low) {
        /* Would the next sector fit? (Structural overrun guard.) */
        if (feeder_fifo_free(f) < f->sector_size) {
            /* Can't happen given fifo_cap >= sector_size and the low-water gate,
             * but if a caller set fifo_low == fifo_cap it could — refuse to
             * overrun and record it. */
            f->fifo_overruns++;
            break;
        }
        /* Is a whole sector available in the ring? All-or-nothing: a partial
         * sector is never delivered (the sd_* seam delivers full sectors). */
        size_t got = ringbuf_get_exact(f->ring, sector, f->sector_size);
        if (got == 0) {
            /* Ring starved: not enough for a full sector. Backpressure: wait. */
            f->stall_starved++;
            break;
        }

        /* Deliver across the sd_* seam, then it lands in the FIFO. */
        if (f->sink) f->sink(sector, f->sector_size, f->sink_user);
        f->fifo_level     += f->sector_size;
        f->sectors_pulled += 1;
        f->bytes_pulled   += f->sector_size;
        delivered         += 1;
    }
    return delivered;
}

size_t feeder_drain(feeder *f, size_t bytes)
{
    size_t take = bytes < f->fifo_level ? bytes : f->fifo_level;
    f->fifo_level   -= take;
    f->bytes_drained += take;
    return take;
}

size_t feeder_tick(feeder *f, size_t drain_bytes)
{
    feeder_drain(f, drain_bytes);
    return feeder_pump(f);
}

void feeder_flush(feeder *f)
{
    f->fifo_level = 0;
}
