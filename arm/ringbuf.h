/*
 * ringbuf.h — single-producer / single-consumer byte ring buffer.
 *
 * This models the ARM-side ring buffer of docs/transport.md §4: the buffer
 * that sits between the TCP recv() (producer) and the sd_* sector pull
 * (consumer). It is the heart of the end-to-end pull pacing — the consumer
 * (FPGA input FIFO via the sd_* seam) drains it, and the producer (TCP) only
 * needs to keep it non-empty:
 *
 *   TCP recv() ──put──► [ring buffer, seconds] ──get(sd_* pull)──► FPGA FIFO
 *
 * Semantics this models explicitly (and that the unit tests pin down):
 *   - capacity            — fixed byte budget (allocated once at init);
 *   - full / empty        — clear, exact counts;
 *   - overrun rejection   — a put() that would exceed free space writes nothing
 *                           and is reported (no silent partial write, no clobber
 *                           of unread data); the caller decides (TCP backpressure
 *                           means the producer simply waits / stops reading);
 *   - underrun            — a get() of more than is available returns only what
 *                           is there and is reported (the consumer must wait);
 *   - low / high water    — thresholds that drive the pull pacing decisions
 *                           ("ring below low-water → producer should fetch more
 *                           from TCP"; "ring at/above high-water → producer can
 *                           back off").
 *
 * OFF-TARGET host logic. No FPGA/MiSTer headers; plain C11 (cc/clang).
 *
 * SPSC contract: exactly one thread may call ringbuf_put* and exactly one (the
 * other, or the same in a single-threaded model) may call ringbuf_get*. The
 * head index is producer-owned, the tail index consumer-owned; counts are
 * derived. The host model here is single-threaded (the tests drive both ends),
 * but the index discipline is the SPSC one so it ports to a lock-free build.
 */
#ifndef NETVOB_ARM_RINGBUF_H
#define NETVOB_ARM_RINGBUF_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t *buf;        /* backing store (caller-owned or rb-owned, see init)  */
    size_t   cap;        /* capacity in bytes (max bytes simultaneously held)   */
    size_t   head;       /* producer write cursor (0 .. cap-1)                  */
    size_t   tail;       /* consumer read cursor  (0 .. cap-1)                  */
    size_t   count;      /* bytes currently stored (0 .. cap)                   */
    size_t   low_water;  /* "ring is running low; fetch more" threshold (bytes) */
    size_t   high_water; /* "ring is well-filled; producer may back off"        */
    int      owns_buf;   /* 1 if rb allocated buf and must free it              */

    /* Lifetime accounting (handy for the pacing model + tests). */
    uint64_t total_put;     /* bytes successfully accepted by put()             */
    uint64_t total_got;     /* bytes successfully handed out by get()           */
    uint64_t overrun_events;/* put() calls rejected for lack of free space      */
    uint64_t underrun_events;/* get() calls that returned fewer than requested  */
} ringbuf;

/*
 * Initialise with a caller-supplied backing store of `cap` bytes. The ring
 * does NOT take ownership; the caller keeps `storage` alive for the ring's
 * lifetime. low/high water are clamped to [0, cap] (and low <= high). Returns
 * 0 on success, -1 on bad args (NULL, cap==0).
 */
int ringbuf_init(ringbuf *rb, uint8_t *storage, size_t cap,
                 size_t low_water, size_t high_water);

/*
 * Initialise with a heap-allocated backing store of `cap` bytes (rb owns it;
 * release with ringbuf_free). Returns 0 on success, -1 on allocation failure
 * or bad args.
 */
int ringbuf_init_alloc(ringbuf *rb, size_t cap,
                       size_t low_water, size_t high_water);

/* Release an rb that owns its buffer (no-op for caller-supplied storage). */
void ringbuf_free(ringbuf *rb);

/* Drop all stored bytes (e.g. on seek/flush, docs/transport.md §6). Indices and
 * counts reset; lifetime/event totals are preserved. */
void ringbuf_clear(ringbuf *rb);

/* ---- queries (const) ---------------------------------------------------- */

size_t ringbuf_count(const ringbuf *rb);  /* bytes currently stored          */
size_t ringbuf_free_space(const ringbuf *rb); /* bytes that put() would accept*/
size_t ringbuf_capacity(const ringbuf *rb);
int    ringbuf_is_empty(const ringbuf *rb);
int    ringbuf_is_full(const ringbuf *rb);
/* count <= low_water (ring is running low; producer should fetch more).      */
int    ringbuf_below_low_water(const ringbuf *rb);
/* count >= high_water (ring is well-filled; producer may back off / TCP can
 * stall via flow control).                                                   */
int    ringbuf_at_high_water(const ringbuf *rb);

/* ---- producer side ------------------------------------------------------ */

/*
 * All-or-nothing put: if `len` exceeds free space, write NOTHING, bump
 * overrun_events, and return 0. Otherwise copy all `len` bytes and return len.
 * This matches TCP backpressure: the producer simply doesn't read from the
 * socket until there is room (no clobbering of unread data).
 */
size_t ringbuf_put(ringbuf *rb, const uint8_t *src, size_t len);

/*
 * Best-effort put: copy as many of `len` bytes as fit (0 .. len) and return
 * that count. If fewer than `len` were taken, bump overrun_events. Never
 * overwrites unread data.
 */
size_t ringbuf_put_some(ringbuf *rb, const uint8_t *src, size_t len);

/* ---- consumer side ------------------------------------------------------ */

/*
 * Best-effort get: copy up to `len` bytes into `dst`, return the count copied
 * (0 .. len). If fewer than `len` were available, bump underrun_events. The
 * consumer (sd_* pull) uses this and simply waits when it underruns.
 */
size_t ringbuf_get(ringbuf *rb, uint8_t *dst, size_t len);

/*
 * All-or-nothing get: if fewer than `len` bytes are stored, copy NOTHING, bump
 * underrun_events, and return 0. Otherwise copy exactly `len` and return len.
 * Useful for fixed-size sector reads that must be complete or not at all.
 */
size_t ringbuf_get_exact(ringbuf *rb, uint8_t *dst, size_t len);

/* Discard up to `len` bytes without copying; return the count discarded. */
size_t ringbuf_skip(ringbuf *rb, size_t len);

#ifdef __cplusplus
}
#endif

#endif /* NETVOB_ARM_RINGBUF_H */
