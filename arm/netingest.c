/*
 * netingest.c — see netingest.h. Glue only; the heavy lifting lives in
 * ps_demux.c / ringbuf.c.
 */
#include "netingest.h"

#include <stdlib.h>

/* ps_demux video-ES sink: append straight into the ring. The ring's put is
 * all-or-nothing per call, so a partial-fit chunk would be rejected wholesale.
 * To avoid dropping a whole burst when only the tail doesn't fit, store as much
 * as fits with ringbuf_put_some() and count the remainder as an overrun. */
static void es_to_ring_sink(const uint8_t *data, size_t len, void *user)
{
    netingest *ni = (netingest *)user;
    ni->st.video_es_bytes += len;
    size_t stored = ringbuf_put_some(&ni->ring, data, len);
    ni->st.es_to_ring += stored;
    if (stored < len)
        ni->st.es_dropped += (len - stored);
}

int ni_init(netingest *ni, size_t ring_cap, size_t sector_size)
{
    if (!ni || ring_cap == 0 || sector_size == 0 || sector_size > ring_cap)
        return -1;
    for (size_t i = 0; i < sizeof(*ni); i++)
        ((uint8_t *)ni)[i] = 0;
    /* low/high water at 1 and cap-1 sector — the recv loop gates on ni_room(). */
    if (ringbuf_init_alloc(&ni->ring, ring_cap, sector_size,
                           ring_cap > sector_size ? ring_cap - sector_size : 0) != 0)
        return -1;
    ni->owns_ring = 1;
    ni->sector_size = sector_size;
    ps_demux_init(&ni->demux, es_to_ring_sink, ni);
    return 0;
}

void ni_free(netingest *ni)
{
    if (!ni) return;
    if (ni->owns_ring) {
        ringbuf_free(&ni->ring);
        ni->owns_ring = 0;
    }
}

size_t ni_feed(netingest *ni, const uint8_t *ps, size_t len)
{
    if (!ni || (!ps && len)) return 0;
    ni->st.bytes_fed += len;
    return ps_demux_feed(&ni->demux, ps, len);
}

size_t ni_get_sector(netingest *ni, uint8_t *dst)
{
    if (!ni || !dst) return 0;
    size_t got = ringbuf_get_exact(&ni->ring, dst, ni->sector_size);
    if (got == ni->sector_size) {
        ni->st.sectors_out++;
        return got;
    }
    ni->st.sector_underruns++;
    return 0;
}

size_t ni_available(const netingest *ni)
{
    return ni ? ringbuf_count(&ni->ring) : 0;
}

size_t ni_room(const netingest *ni)
{
    return ni ? ringbuf_free_space(&ni->ring) : 0;
}

void ni_flush(netingest *ni)
{
    if (ni) ringbuf_clear(&ni->ring);
}

const ni_stats *ni_get_stats(const netingest *ni)
{
    return ni ? &ni->st : NULL;
}
