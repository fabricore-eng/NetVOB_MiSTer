/*
 * netingest.{h,c} — the network-ingest glue for the console HPS app.
 *
 * Ties the three host-tested components into the live spine seam (docs/transport.md
 * §4): TCP recv() -> ps_demux -> video ES -> ring -> (sd_* sector pull).
 *
 *      socket bytes ──ni_feed()──► ps_demux ──video ES──► ringbuf
 *                                  (audio/nav/pad dropped)     │
 *      FPGA sd_rd request ──ni_get_sector()──◄────────────────┘
 *
 * The two sides are decoupled by the ring so the network producer and the sd_*
 * consumer run at independent rates (the compressed PS is < ~2 MB/s, far below
 * the sector path). The sd_* seam itself stays abstracted: on hardware,
 * ni_get_sector() feeds the sector into sd_buff_dout/sd_buff_wr; off-target it
 * just returns the bytes. No FPGA/MiSTer headers — plain C11, host-buildable.
 *
 * Backpressure: ni_feed() never clobbers unread data. If the ring can't hold the
 * video ES a chunk produces (consumer too slow), the overflow is COUNTED
 * (ring_overruns / es_dropped) and the offending bytes are dropped — the caller's
 * recv loop is expected to gate on ni_room() so this stays at zero in normal
 * operation (that is the TCP backpressure point).
 */
#ifndef NETVOB_ARM_NETINGEST_H
#define NETVOB_ARM_NETINGEST_H

#include <stddef.h>
#include <stdint.h>

#include "ps_demux.h"
#include "ringbuf.h"

typedef struct {
    uint64_t bytes_fed;       /* PS bytes handed to ni_feed()                  */
    uint64_t video_es_bytes;  /* video ES the demux produced                   */
    uint64_t es_to_ring;      /* video ES bytes actually stored in the ring    */
    uint64_t es_dropped;      /* video ES bytes dropped (ring full = overrun)  */
    uint64_t sectors_out;     /* full sectors handed out via ni_get_sector()   */
    uint64_t sector_underruns;/* ni_get_sector() calls with < 1 full sector    */
} ni_stats;

typedef struct {
    ringbuf   ring;           /* video-ES ring (post-demux)                     */
    ps_demux  demux;          /* PS -> video ES                                 */
    size_t    sector_size;    /* sd_* sector granularity (e.g. 2048)            */
    ni_stats  st;
    int       owns_ring;      /* ring storage was heap-allocated by ni_init     */
} netingest;

/*
 * Initialise with a heap-allocated video-ES ring of `ring_cap` bytes and a
 * `sector_size` sd_* granularity. Returns 0 on success, -1 on allocation/arg
 * error. Call ni_free() when done.
 */
int  ni_init(netingest *ni, size_t ring_cap, size_t sector_size);
void ni_free(netingest *ni);

/*
 * Feed `len` bytes received off the socket. They flow through the PS demux; the
 * extracted video ES is appended to the ring (audio/nav/padding dropped).
 * Returns the number of PS bytes consumed by the demux (always `len`).
 */
size_t ni_feed(netingest *ni, const uint8_t *ps, size_t len);

/*
 * Pull exactly one sector of video ES for an sd_* request into `dst`
 * (sector_size bytes). Returns sector_size when a whole sector was available,
 * else 0 (underrun: the ring has < 1 sector right now — the caller decides
 * whether to stall the sd_* ack or zero-pad). All-or-nothing: on underrun the
 * ring is left untouched.
 */
size_t ni_get_sector(netingest *ni, uint8_t *dst);

/* Video ES bytes currently queued, and ring free space (the recv-gate signal). */
size_t ni_available(const netingest *ni);
size_t ni_room(const netingest *ni);

/* Drop any buffered ES (seek / channel change) — flush the ring. */
void ni_flush(netingest *ni);

const ni_stats *ni_get_stats(const netingest *ni);

#endif /* NETVOB_ARM_NETINGEST_H */
