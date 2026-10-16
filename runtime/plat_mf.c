/* Linux build: the Media Foundation subset of plat/mfapi.h on FFmpeg (libavformat demuxes the in-memory file,
 * libavcodec decodes, libswscale repacks video to NV12, libswresample makes 16-bit interleaved PCM).
 *
 * What nnmovie_mf.c relies on, as Media Foundation's source reader does it:
 *   - ReadSample(stream) returns the next DECODED sample of that stream (time in 100 ns), or the end-of-stream flag;
 *     samples of the other selected streams met on the way are kept for their own calls, unselected ones dropped;
 *   - video comes out as tightly packed NV12 (Y rows of w bytes, then (h+1)/2 rows of interleaved UV), audio as
 *     16-bit PCM at the stream's own rate and channel count unless the caller asked for others;
 *   - SetCurrentPosition seeks to the key frame at or before the time and forgets what was buffered. */
#define _GNU_SOURCE
#include "plat/mfapi.h"
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libswscale/swscale.h>
#include <libswresample/swresample.h>
#include <libavutil/channel_layout.h>
#include <libavutil/opt.h>

#undef fopen

#define FCC_GUID(a, b, c, d) {(uint32_t)(a) | ((uint32_t)(b) << 8) | ((uint32_t)(c) << 16) | ((uint32_t)(d) << 24), 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}}
#define NUM_GUID(n) {(uint32_t)(n), 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}}
const GUID GUID_NULL = {0, 0, 0, {0}};
const GUID IID_IMFAttributes = {0x2cd2d921, 0xc447, 0x44a7, {0xa1, 0x3c, 0x4a, 0xda, 0xbf, 0xc2, 0x47, 0xe3}};
const GUID IID_IMF2DBuffer = {0x7dc9d5f9, 0x9ed9, 0x44ec, {0x9b, 0xbf, 0x06, 0x80, 0xf7, 0xa6, 0xd6, 0xc3}};
const GUID MF_MT_MAJOR_TYPE = {0x48eba18e, 0xf8c9, 0x4687, {0xbf, 0x11, 0x0a, 0x74, 0xc9, 0xf9, 0x6a, 0x8f}};
const GUID MF_MT_SUBTYPE = {0xf7e34c9a, 0x42e8, 0x4714, {0xb7, 0x4b, 0xcb, 0x29, 0xd7, 0x2c, 0x35, 0xe5}};
const GUID MF_MT_FRAME_SIZE = {0x1652c33d, 0xd6b2, 0x4012, {0xb8, 0x34, 0x72, 0x03, 0x08, 0x49, 0xa3, 0x7d}};
const GUID MF_MT_FRAME_RATE = {0xc459a2e8, 0x3d2c, 0x4e44, {0xb1, 0x32, 0xfe, 0xe5, 0x15, 0x6c, 0x7b, 0xb0}};
const GUID MF_MT_AUDIO_SAMPLES_PER_SECOND = {0x5faeeae7, 0x0290, 0x4c31, {0x9e, 0x8a, 0xc5, 0x34, 0xf6, 0x8d, 0x9d, 0xba}};
const GUID MF_MT_AUDIO_NUM_CHANNELS = {0x37e48bf5, 0x645e, 0x4c5b, {0x89, 0xde, 0xad, 0xa9, 0xe2, 0x9b, 0x69, 0x6a}};
const GUID MF_MT_AUDIO_BITS_PER_SAMPLE = {0xf2deb57f, 0x40fa, 0x4764, {0xaa, 0x33, 0xed, 0x4f, 0x2d, 0x1f, 0xf6, 0x69}};
const GUID MF_PD_DURATION = {0x6c990d33, 0xbb8e, 0x477a, {0x85, 0x98, 0x0d, 0x5d, 0x96, 0xfc, 0xd8, 0x8a}};
const GUID MF_BYTESTREAM_CONTENT_TYPE = {0xfc358289, 0x3cb6, 0x460c, {0xa4, 0x24, 0xb6, 0x68, 0x12, 0x60, 0x37, 0x5a}};
const GUID MF_BYTESTREAM_ORIGIN_NAME = {0xfc358288, 0x3cb6, 0x460c, {0xa4, 0x24, 0xb6, 0x68, 0x12, 0x60, 0x37, 0x5a}};
const GUID MFMediaType_Video = FCC_GUID('v', 'i', 'd', 's');
const GUID MFMediaType_Audio = FCC_GUID('a', 'u', 'd', 's');
const GUID MFVideoFormat_NV12 = FCC_GUID('N', 'V', '1', '2');
const GUID MFAudioFormat_PCM = NUM_GUID(1);
static const GUID k_vorbis = {0x8D2FD10B, 0x5841, 0x4a6b, {0x89, 0x05, 0x58, 0x8F, 0xEC, 0x1A, 0xDE, 0xD9}};
#define MF_E_NO_MORE_TYPES ((HRESULT)0xC00D36B9)
#define MF_E_INVALIDREQUEST ((HRESULT)0xC00D36B2)

HRESULT MFStartup(ULONG version, DWORD flags) { (void)version; (void)flags; return S_OK; }

/* ---- memory stream, byte stream, attributes */
struct IStream { volatile LONG refs; uint8_t *data; size_t size; };
struct IMFAttributes { volatile LONG refs; };
struct IMFByteStream { volatile LONG refs; IStream *st; IMFAttributes attrs; };
IStream *SHCreateMemStream(const BYTE *data, UINT size) {
    IStream *s = calloc(1, sizeof *s);
    s->refs = 1; s->size = size;
    s->data = av_malloc(size ? size : 1);
    if (!s->data) { free(s); return NULL; }
    if (size) memcpy(s->data, data, size);
    return s;
}
ULONG IStream_Release(IStream *s) { LONG n = InterlockedDecrement(&s->refs); if (!n) { av_free(s->data); free(s); } return (ULONG)n; }
HRESULT MFCreateMFByteStreamOnStream(IStream *s, IMFByteStream **out) {
    IMFByteStream *b = calloc(1, sizeof *b);
    b->refs = 1; b->st = s; InterlockedIncrement(&s->refs);
    *out = b;
    return S_OK;
}
HRESULT IMFByteStream_QueryInterface(IMFByteStream *b, const GUID *iid, void **out) {
    if (IsEqualGUID(iid, &IID_IMFAttributes)) { InterlockedIncrement(&b->refs); *out = &b->attrs; return S_OK; }
    *out = NULL; return E_NOINTERFACE;
}
ULONG IMFByteStream_Release(IMFByteStream *b) { LONG n = InterlockedDecrement(&b->refs); if (!n) { IStream_Release(b->st); free(b); } return (ULONG)n; }
HRESULT IMFAttributes_SetString(IMFAttributes *a, const GUID *key, LPCWSTR value) { (void)a; (void)key; (void)value; return S_OK; }      /* the demuxer probes the bytes itself */
ULONG IMFAttributes_Release(IMFAttributes *a) { return IMFByteStream_Release((IMFByteStream *)((uint8_t *)a - offsetof(IMFByteStream, attrs))); }

/* ---- media types: a small attribute list */
typedef struct { GUID key; int kind; UINT64 u; GUID g; } MTAttr;    /* kind 1 u32, 2 u64, 3 guid */
struct IMFMediaType { volatile LONG refs; int n; MTAttr a[16]; };
HRESULT MFCreateMediaType(IMFMediaType **out) { IMFMediaType *t = calloc(1, sizeof *t); t->refs = 1; *out = t; return S_OK; }
ULONG IMFMediaType_Release(IMFMediaType *t) { LONG n = InterlockedDecrement(&t->refs); if (!n) free(t); return (ULONG)n; }
static MTAttr *mt_find(IMFMediaType *t, const GUID *k, int add) {
    for (int i = 0; i < t->n; i++) if (IsEqualGUID(&t->a[i].key, k)) return &t->a[i];
    if (!add || t->n == 16) return NULL;
    memset(&t->a[t->n], 0, sizeof t->a[0]);
    t->a[t->n].key = *k;
    return &t->a[t->n++];
}
HRESULT IMFMediaType_SetGUID(IMFMediaType *t, const GUID *k, const GUID *v) { MTAttr *a = mt_find(t, k, 1); if (!a) return E_OUTOFMEMORY; a->kind = 3; a->g = *v; return S_OK; }
HRESULT IMFMediaType_GetGUID(IMFMediaType *t, const GUID *k, GUID *v) { MTAttr *a = mt_find(t, k, 0); if (!a || a->kind != 3) return MF_E_ATTRIBUTENOTFOUND; *v = a->g; return S_OK; }
HRESULT IMFMediaType_SetUINT32(IMFMediaType *t, const GUID *k, UINT32 v) { MTAttr *a = mt_find(t, k, 1); if (!a) return E_OUTOFMEMORY; a->kind = 1; a->u = v; return S_OK; }
HRESULT IMFMediaType_GetUINT32(IMFMediaType *t, const GUID *k, UINT32 *v) { MTAttr *a = mt_find(t, k, 0); if (!a || a->kind != 1) return MF_E_ATTRIBUTENOTFOUND; *v = (UINT32)a->u; return S_OK; }
HRESULT IMFMediaType_SetUINT64(IMFMediaType *t, const GUID *k, UINT64 v) { MTAttr *a = mt_find(t, k, 1); if (!a) return E_OUTOFMEMORY; a->kind = 2; a->u = v; return S_OK; }
HRESULT IMFMediaType_GetUINT64(IMFMediaType *t, const GUID *k, UINT64 *v) { MTAttr *a = mt_find(t, k, 0); if (!a || a->kind != 2) return MF_E_ATTRIBUTENOTFOUND; *v = a->u; return S_OK; }

/* ---- samples and buffers: one contiguous block each */
struct IMFSample { volatile LONG refs; uint8_t *data; DWORD len; };
struct IMFMediaBuffer { volatile LONG refs; IMFSample *s; };
static IMFSample *sample_new(size_t len) {
    IMFSample *s = calloc(1, sizeof *s);
    s->refs = 1; s->len = (DWORD)len; s->data = malloc(len ? len : 1);
    return s;
}
ULONG IMFSample_Release(IMFSample *s) { LONG n = InterlockedDecrement(&s->refs); if (!n) { free(s->data); free(s); } return (ULONG)n; }
HRESULT IMFSample_ConvertToContiguousBuffer(IMFSample *s, IMFMediaBuffer **out) {
    IMFMediaBuffer *b = calloc(1, sizeof *b);
    b->refs = 1; b->s = s; InterlockedIncrement(&s->refs);
    *out = b;
    return S_OK;
}
HRESULT IMFMediaBuffer_Lock(IMFMediaBuffer *b, BYTE **data, DWORD *max, DWORD *len) { *data = b->s->data; if (max) *max = b->s->len; if (len) *len = b->s->len; return S_OK; }
HRESULT IMFMediaBuffer_Unlock(IMFMediaBuffer *b) { (void)b; return S_OK; }
ULONG IMFMediaBuffer_Release(IMFMediaBuffer *b) { LONG n = InterlockedDecrement(&b->refs); if (!n) { IMFSample_Release(b->s); free(b); } return (ULONG)n; }
HRESULT IMFMediaBuffer_QueryInterface(IMFMediaBuffer *b, const GUID *iid, void **out) { (void)b; (void)iid; *out = NULL; return E_NOINTERFACE; }
HRESULT IMF2DBuffer_Lock2D(IMF2DBuffer *b, BYTE **scan0, LONG *pitch) { (void)b; (void)scan0; (void)pitch; return E_NOINTERFACE; }
HRESULT IMF2DBuffer_Unlock2D(IMF2DBuffer *b) { (void)b; return S_OK; }
ULONG IMF2DBuffer_Release(IMF2DBuffer *b) { (void)b; return 0; }

/* ---- the source reader */
#define RD_STREAMS 8
typedef struct { AVPacket **p; int n, cap; } PktQ;
typedef struct {
    AVStream *st;
    AVCodecContext *cc;
    int selected, flushed, open_failed;
    int out_rate, out_ch;                             /* audio output asked for (0 = the stream's own) */
    struct SwsContext *sws;
    SwrContext *swr;
    int swr_in_rate, swr_in_fmt, swr_in_ch;
    PktQ q;
} RStream;
struct IMFSourceReader {
    volatile LONG refs;
    IMFByteStream *bs;
    int64_t pos;
    AVIOContext *io;
    AVFormatContext *fmt;
    int n, demux_eof;
    RStream s[RD_STREAMS];
    AVFrame *frame;
    AVPacket *pkt;
};
static int mem_read(void *opaque, uint8_t *buf, int n) {
    IMFSourceReader *r = opaque;
    IStream *st = r->bs->st;
    if (r->pos >= (int64_t)st->size) return AVERROR_EOF;
    if ((int64_t)n > (int64_t)st->size - r->pos) n = (int)((int64_t)st->size - r->pos);
    memcpy(buf, st->data + r->pos, (size_t)n);
    r->pos += n;
    return n;
}
static int64_t mem_seek(void *opaque, int64_t off, int whence) {
    IMFSourceReader *r = opaque;
    int64_t size = (int64_t)r->bs->st->size, p;
    if (whence == AVSEEK_SIZE) return size;
    whence &= ~AVSEEK_FORCE;
    p = whence == SEEK_SET ? off : whence == SEEK_CUR ? r->pos + off : size + off;
    if (p < 0 || p > size) return -1;
    r->pos = p;
    return p;
}
static void q_push(PktQ *q, AVPacket *p) {
    if (q->n == q->cap) { q->cap = q->cap ? q->cap * 2 : 32; q->p = realloc(q->p, (size_t)q->cap * sizeof *q->p); }
    q->p[q->n++] = p;
}
static AVPacket *q_pop(PktQ *q) {
    if (!q->n) return NULL;
    AVPacket *p = q->p[0];
    memmove(q->p, q->p + 1, (size_t)(q->n - 1) * sizeof *q->p);
    q->n--;
    return p;
}
static void q_clear(PktQ *q) { AVPacket *p; while ((p = q_pop(q))) av_packet_free(&p); }

HRESULT MFCreateSourceReaderFromByteStream(IMFByteStream *b, IMFAttributes *attrs, IMFSourceReader **out) {
    (void)attrs;
    *out = NULL;
    IMFSourceReader *r = calloc(1, sizeof *r);
    r->refs = 1; r->bs = b; InterlockedIncrement(&b->refs);
    uint8_t *iobuf = av_malloc(1 << 16);
    r->io = avio_alloc_context(iobuf, 1 << 16, 0, r, mem_read, NULL, mem_seek);
    r->fmt = avformat_alloc_context();
    r->fmt->pb = r->io;
    r->fmt->flags |= AVFMT_FLAG_CUSTOM_IO;
    int e = avformat_open_input(&r->fmt, NULL, NULL, NULL);
    if (e < 0 || avformat_find_stream_info(r->fmt, NULL) < 0) {
        char m[128]; av_strerror(e, m, sizeof m);
        printf("movie (FFmpeg): the file cannot be opened: %s\n", m);
        IMFSourceReader_Release(r);
        return MF_E_UNSUPPORTED_BYTESTREAM_TYPE;
    }
    r->n = (int)r->fmt->nb_streams < RD_STREAMS ? (int)r->fmt->nb_streams : RD_STREAMS;
    for (int i = 0; i < r->n; i++) { r->s[i].st = r->fmt->streams[i]; r->s[i].selected = 1; }      /* as MF: the first streams start selected */
    r->frame = av_frame_alloc();
    r->pkt = av_packet_alloc();
    *out = r;
    return S_OK;
}
ULONG IMFSourceReader_Release(IMFSourceReader *r) {
    LONG n = InterlockedDecrement(&r->refs);
    if (n) return (ULONG)n;
    for (int i = 0; i < RD_STREAMS; i++) {
        RStream *s = &r->s[i];
        q_clear(&s->q); free(s->q.p);
        if (s->cc) avcodec_free_context(&s->cc);
        if (s->sws) sws_freeContext(s->sws);
        if (s->swr) swr_free(&s->swr);
    }
    if (r->fmt) avformat_close_input(&r->fmt);
    if (r->io) { av_freep(&r->io->buffer); avio_context_free(&r->io); }
    av_frame_free(&r->frame);
    av_packet_free(&r->pkt);
    IMFByteStream_Release(r->bs);
    free(r);
    return 0;
}
HRESULT IMFSourceReader_GetPresentationAttribute(IMFSourceReader *r, DWORD stream, const GUID *key, PROPVARIANT *out) {
    if (stream != MF_SOURCE_READER_MEDIASOURCE || !IsEqualGUID(key, &MF_PD_DURATION) || r->fmt->duration == AV_NOPTS_VALUE) return MF_E_ATTRIBUTENOTFOUND;
    out->vt = VT_UI8;
    out->uhVal.QuadPart = (ULONGLONG)r->fmt->duration * 10;          /* AV_TIME_BASE (us) -> 100 ns */
    return S_OK;
}
static void stream_reset(RStream *s) {
    q_clear(&s->q);
    if (s->cc) avcodec_flush_buffers(s->cc);
    s->flushed = 0;
}
HRESULT IMFSourceReader_SetStreamSelection(IMFSourceReader *r, DWORD stream, BOOL on) {
    if (stream == MF_SOURCE_READER_ALL_STREAMS) { for (int i = 0; i < r->n; i++) { r->s[i].selected = on; if (!on) q_clear(&r->s[i].q); } return S_OK; }
    if ((int)stream >= r->n) return MF_E_INVALIDSTREAMNUMBER;
    r->s[stream].selected = on;
    if (!on) q_clear(&r->s[stream].q);
    return S_OK;
}
static GUID subtype_of(const AVCodecParameters *p) {
    static const GUID vp9 = FCC_GUID('V', 'P', '9', '0'), vp8 = FCC_GUID('V', 'P', '8', '0'), h264 = FCC_GUID('H', '2', '6', '4'), hevc = FCC_GUID('H', 'E', 'V', 'C'),
                      av1 = FCC_GUID('A', 'V', '0', '1'), opus = NUM_GUID(0x704F), aac = NUM_GUID(0x1610), mp3 = NUM_GUID(0x55), pcm = NUM_GUID(1);
    switch (p->codec_id) {
    case AV_CODEC_ID_VP9: return vp9;
    case AV_CODEC_ID_VP8: return vp8;
    case AV_CODEC_ID_H264: return h264;
    case AV_CODEC_ID_HEVC: return hevc;
    case AV_CODEC_ID_AV1: return av1;
    case AV_CODEC_ID_VORBIS: return k_vorbis;
    case AV_CODEC_ID_OPUS: return opus;
    case AV_CODEC_ID_AAC: return aac;
    case AV_CODEC_ID_MP3: return mp3;
    case AV_CODEC_ID_PCM_S16LE: return pcm;
    default: { GUID g = GUID_NULL; g.Data1 = (uint32_t)p->codec_id; return g; }
    }
}
HRESULT IMFSourceReader_GetNativeMediaType(IMFSourceReader *r, DWORD stream, DWORD index, IMFMediaType **out) {
    if ((int)stream >= r->n) return MF_E_INVALIDSTREAMNUMBER;
    if (index) return MF_E_NO_MORE_TYPES;
    const AVStream *st = r->s[stream].st;
    const AVCodecParameters *p = st->codecpar;
    IMFMediaType *t;
    MFCreateMediaType(&t);
    GUID sub = subtype_of(p);
    if (p->codec_type == AVMEDIA_TYPE_VIDEO) {
        IMFMediaType_SetGUID(t, &MF_MT_MAJOR_TYPE, &MFMediaType_Video);
        AVRational fr = st->avg_frame_rate.num ? st->avg_frame_rate : st->r_frame_rate;
        IMFMediaType_SetUINT64(t, &MF_MT_FRAME_SIZE, ((UINT64)(UINT32)p->width << 32) | (UINT32)p->height);
        if (fr.num && fr.den) IMFMediaType_SetUINT64(t, &MF_MT_FRAME_RATE, ((UINT64)(UINT32)fr.num << 32) | (UINT32)fr.den);
    } else if (p->codec_type == AVMEDIA_TYPE_AUDIO) {
        IMFMediaType_SetGUID(t, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
        IMFMediaType_SetUINT32(t, &MF_MT_AUDIO_SAMPLES_PER_SECOND, (UINT32)p->sample_rate);
        IMFMediaType_SetUINT32(t, &MF_MT_AUDIO_NUM_CHANNELS, (UINT32)p->ch_layout.nb_channels);
    } else {
        static const GUID other = {0x73747874, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71}};
        IMFMediaType_SetGUID(t, &MF_MT_MAJOR_TYPE, &other);
    }
    IMFMediaType_SetGUID(t, &MF_MT_SUBTYPE, &sub);
    *out = t;
    return S_OK;
}
static int open_decoder(RStream *s) {
    if (s->cc) return 1;
    if (s->open_failed) return 0;
    const AVCodec *codec = avcodec_find_decoder(s->st->codecpar->codec_id);
    if (codec && (s->cc = avcodec_alloc_context3(codec))) {
        avcodec_parameters_to_context(s->cc, s->st->codecpar);
        s->cc->pkt_timebase = s->st->time_base;
        s->cc->thread_count = 0;                      /* as many as FFmpeg finds useful */
        if (avcodec_open2(s->cc, codec, NULL) == 0) return 1;
        avcodec_free_context(&s->cc);
    }
    s->open_failed = 1;
    printf("movie (FFmpeg): no decoder for %s\n", avcodec_get_name(s->st->codecpar->codec_id));
    return 0;
}
HRESULT IMFSourceReader_SetCurrentMediaType(IMFSourceReader *r, DWORD stream, DWORD *reserved, IMFMediaType *t) {
    (void)reserved;
    if ((int)stream >= r->n) return MF_E_INVALIDSTREAMNUMBER;
    RStream *s = &r->s[stream];
    GUID major = GUID_NULL, sub = GUID_NULL;
    IMFMediaType_GetGUID(t, &MF_MT_MAJOR_TYPE, &major);
    IMFMediaType_GetGUID(t, &MF_MT_SUBTYPE, &sub);
    int video = s->st->codecpar->codec_type == AVMEDIA_TYPE_VIDEO, audio = s->st->codecpar->codec_type == AVMEDIA_TYPE_AUDIO;
    if (video && !IsEqualGUID(&sub, &MFVideoFormat_NV12)) return MF_E_INVALIDMEDIATYPE;
    if (audio) {
        UINT32 bits = 16, rate = 0, ch = 0;
        if (!IsEqualGUID(&sub, &MFAudioFormat_PCM)) return MF_E_INVALIDMEDIATYPE;
        IMFMediaType_GetUINT32(t, &MF_MT_AUDIO_BITS_PER_SAMPLE, &bits);
        if (bits != 16) return MF_E_INVALIDMEDIATYPE;
        IMFMediaType_GetUINT32(t, &MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
        IMFMediaType_GetUINT32(t, &MF_MT_AUDIO_NUM_CHANNELS, &ch);
        s->out_rate = (int)rate; s->out_ch = (int)ch;
    }
    if (!video && !audio) return MF_E_INVALIDMEDIATYPE;
    return open_decoder(s) ? S_OK : MF_E_INVALIDMEDIATYPE;
}
HRESULT IMFSourceReader_GetCurrentMediaType(IMFSourceReader *r, DWORD stream, IMFMediaType **out) {
    if ((int)stream >= r->n) return MF_E_INVALIDSTREAMNUMBER;
    RStream *s = &r->s[stream];
    const AVCodecParameters *p = s->st->codecpar;
    IMFMediaType *t;
    MFCreateMediaType(&t);
    if (p->codec_type == AVMEDIA_TYPE_VIDEO) {
        IMFMediaType_SetGUID(t, &MF_MT_MAJOR_TYPE, &MFMediaType_Video);
        IMFMediaType_SetGUID(t, &MF_MT_SUBTYPE, &MFVideoFormat_NV12);
        IMFMediaType_SetUINT64(t, &MF_MT_FRAME_SIZE, ((UINT64)(UINT32)p->width << 32) | (UINT32)p->height);
    } else {
        IMFMediaType_SetGUID(t, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
        IMFMediaType_SetGUID(t, &MF_MT_SUBTYPE, &MFAudioFormat_PCM);
        IMFMediaType_SetUINT32(t, &MF_MT_AUDIO_BITS_PER_SAMPLE, 16);
        IMFMediaType_SetUINT32(t, &MF_MT_AUDIO_SAMPLES_PER_SECOND, (UINT32)(s->out_rate ? s->out_rate : p->sample_rate));
        IMFMediaType_SetUINT32(t, &MF_MT_AUDIO_NUM_CHANNELS, (UINT32)(s->out_ch ? s->out_ch : p->ch_layout.nb_channels));
    }
    *out = t;
    return S_OK;
}
HRESULT IMFSourceReader_SetCurrentPosition(IMFSourceReader *r, const GUID *format, const PROPVARIANT *pos) {
    (void)format;
    int64_t us = pos->hVal.QuadPart / 10;
    if (r->fmt->start_time != AV_NOPTS_VALUE) us += r->fmt->start_time;
    int e = av_seek_frame(r->fmt, -1, us, AVSEEK_FLAG_BACKWARD);
    for (int i = 0; i < r->n; i++) stream_reset(&r->s[i]);
    r->demux_eof = 0;
    return e < 0 ? E_FAIL : S_OK;
}
/* One more packet from the file into the queue of its stream (dropped when the stream is not selected). */
static void demux_one(IMFSourceReader *r) {
    if (av_read_frame(r->fmt, r->pkt) < 0) { r->demux_eof = 1; return; }
    int i = r->pkt->stream_index;
    if (i < r->n && r->s[i].selected) { AVPacket *p = av_packet_alloc(); av_packet_move_ref(p, r->pkt); q_push(&r->s[i].q, p); }
    else av_packet_unref(r->pkt);
}
static IMFSample *convert_video(RStream *s, const AVFrame *f) {
    int w = f->width, h = f->height, uvrows = (h + 1) / 2;
    IMFSample *out = sample_new((size_t)w * h + (size_t)w * uvrows);
    s->sws = sws_getCachedContext(s->sws, w, h, (enum AVPixelFormat)f->format, w, h, AV_PIX_FMT_NV12, SWS_POINT, NULL, NULL, NULL);
    if (!s->sws) { IMFSample_Release(out); return NULL; }
    uint8_t *dst[4] = {out->data, out->data + (size_t)w * h, NULL, NULL};
    int pitch[4] = {w, w, 0, 0};
    sws_scale(s->sws, (const uint8_t *const *)f->data, f->linesize, 0, h, dst, pitch);
    return out;
}
static IMFSample *convert_audio(RStream *s, const AVFrame *f) {
    int rate = s->out_rate ? s->out_rate : f->sample_rate, ch = s->out_ch ? s->out_ch : f->ch_layout.nb_channels;
    if (!s->swr || s->swr_in_rate != f->sample_rate || s->swr_in_fmt != f->format || s->swr_in_ch != f->ch_layout.nb_channels) {
        AVChannelLayout out_layout;
        av_channel_layout_default(&out_layout, ch);
        if (s->swr) swr_free(&s->swr);
        if (swr_alloc_set_opts2(&s->swr, &out_layout, AV_SAMPLE_FMT_S16, rate, &f->ch_layout, (enum AVSampleFormat)f->format, f->sample_rate, 0, NULL) < 0 || swr_init(s->swr) < 0) {
            if (s->swr) swr_free(&s->swr);
            return NULL;
        }
        s->swr_in_rate = f->sample_rate; s->swr_in_fmt = f->format; s->swr_in_ch = f->ch_layout.nb_channels;
    }
    int cap = (int)av_rescale_rnd(swr_get_delay(s->swr, f->sample_rate) + f->nb_samples, rate, f->sample_rate, AV_ROUND_UP) + 16;
    IMFSample *out = sample_new((size_t)cap * ch * 2);
    uint8_t *dst = out->data;
    int got = swr_convert(s->swr, &dst, cap, (const uint8_t **)f->extended_data, f->nb_samples);
    if (got < 0) { IMFSample_Release(out); return NULL; }
    out->len = (DWORD)(got * ch * 2);
    return out;
}
HRESULT IMFSourceReader_ReadSample(IMFSourceReader *r, DWORD stream, DWORD control, DWORD *actual, DWORD *flags, LONGLONG *time, IMFSample **out) {
    (void)control;
    *out = NULL; *flags = 0; if (time) *time = 0;
    if ((int)stream >= r->n) return MF_E_INVALIDSTREAMNUMBER;
    if (actual) *actual = stream;
    RStream *s = &r->s[stream];
    if (!s->selected) return MF_E_INVALIDREQUEST;
    if (!open_decoder(s)) { *flags = MF_SOURCE_READERF_ERROR; return E_FAIL; }
    for (;;) {
        int e = avcodec_receive_frame(s->cc, r->frame);
        if (e == 0) {
            IMFSample *smp = s->st->codecpar->codec_type == AVMEDIA_TYPE_VIDEO ? convert_video(s, r->frame) : convert_audio(s, r->frame);
            int64_t ts = r->frame->best_effort_timestamp;
            if (ts == AV_NOPTS_VALUE) ts = r->frame->pts;
            if (ts != AV_NOPTS_VALUE && s->st->start_time != AV_NOPTS_VALUE) ts -= s->st->start_time;
            if (time) *time = ts == AV_NOPTS_VALUE ? 0 : av_rescale_q(ts, s->st->time_base, (AVRational){1, 10000000});
            av_frame_unref(r->frame);
            if (!smp) { *flags = MF_SOURCE_READERF_ERROR; return E_FAIL; }
            *out = smp;
            return S_OK;
        }
        if (e == AVERROR_EOF) { *flags = MF_SOURCE_READERF_ENDOFSTREAM; return S_OK; }
        if (e != AVERROR(EAGAIN)) { *flags = MF_SOURCE_READERF_ERROR; return E_FAIL; }
        AVPacket *p = q_pop(&s->q);
        if (!p) {
            if (r->demux_eof) {
                if (s->flushed) { *flags = MF_SOURCE_READERF_ENDOFSTREAM; return S_OK; }
                avcodec_send_packet(s->cc, NULL);     /* drain the decoder */
                s->flushed = 1;
            } else demux_one(r);
            continue;
        }
        e = avcodec_send_packet(s->cc, p);
        if (e == AVERROR(EAGAIN)) {                   /* output first: the packet goes back to the front of the queue */
            q_push(&s->q, p);
            memmove(s->q.p + 1, s->q.p, (size_t)(s->q.n - 1) * sizeof *s->q.p);
            s->q.p[0] = p;
            continue;
        }
        av_packet_free(&p);
        if (e < 0 && e != AVERROR_INVALIDDATA) { *flags = MF_SOURCE_READERF_ERROR; return E_FAIL; }
    }
}
