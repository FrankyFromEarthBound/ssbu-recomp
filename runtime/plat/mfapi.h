/* Linux build: the Media Foundation subset nnmovie_mf.c uses (source reader over an in-memory byte stream, decoded
 * NV12 video and 16-bit PCM audio), implemented on FFmpeg's libavformat / libavcodec in plat_mf.c. mfidl.h,
 * mfreadwrite.h and mferror.h resolve to this file. Interfaces are opaque structures; the COBJMACROS-style calls of
 * the Windows headers are plain functions here. */
#ifndef PLAT_MFAPI_H
#define PLAT_MFAPI_H
#include "windows.h"

typedef struct IStream IStream;
typedef struct IMFByteStream IMFByteStream;
typedef struct IMFAttributes IMFAttributes;
typedef struct IMFSourceReader IMFSourceReader;
typedef struct IMFMediaType IMFMediaType;
typedef struct IMFSample IMFSample;
typedef struct IMFMediaBuffer IMFMediaBuffer;
typedef struct IMF2DBuffer IMF2DBuffer;
typedef unsigned short VARTYPE;
typedef struct { VARTYPE vt; WORD r1, r2, r3; union { LARGE_INTEGER hVal; ULARGE_INTEGER uhVal; LONG lVal; ULONG ulVal; double dblVal; void *ptr; }; } PROPVARIANT;
#define VT_EMPTY 0
#define VT_I8 20
#define VT_UI8 21
#define PropVariantInit(p) memset((p), 0, sizeof(PROPVARIANT))
#define PropVariantClear(p) (memset((p), 0, sizeof(PROPVARIANT)), S_OK)

#define CoInitializeEx(r, f) S_OK
#define COINIT_MULTITHREADED 0
#define MF_VERSION 0x20070
#define MFSTARTUP_FULL 0
HRESULT MFStartup(ULONG version, DWORD flags);

#define E_NOINTERFACE ((HRESULT)0x80004002)
#define E_OUTOFMEMORY ((HRESULT)0x8007000E)
#define MF_E_INVALIDSTREAMNUMBER ((HRESULT)0xC00D36B3)
#define MF_E_INVALIDMEDIATYPE ((HRESULT)0xC00D36B4)
#define MF_E_ATTRIBUTENOTFOUND ((HRESULT)0xC00D36E6)
#define MF_E_UNSUPPORTED_BYTESTREAM_TYPE ((HRESULT)0xC00D36C4)

#define MF_SOURCE_READER_ALL_STREAMS 0xFFFFFFFEu
#define MF_SOURCE_READER_ANY_STREAM 0xFFFFFFFEu
#define MF_SOURCE_READER_FIRST_VIDEO_STREAM 0xFFFFFFFCu
#define MF_SOURCE_READER_FIRST_AUDIO_STREAM 0xFFFFFFFDu
#define MF_SOURCE_READER_MEDIASOURCE 0xFFFFFFFFu
#define MF_SOURCE_READERF_ERROR 0x1
#define MF_SOURCE_READERF_ENDOFSTREAM 0x2
#define MF_SOURCE_READERF_STREAMTICK 0x100

/* the GUIDs this subset knows (values of the real ones; only their identity matters here) */
extern const GUID GUID_NULL, IID_IMFAttributes, IID_IMF2DBuffer;
extern const GUID MF_MT_MAJOR_TYPE, MF_MT_SUBTYPE, MF_MT_FRAME_SIZE, MF_MT_FRAME_RATE, MF_MT_AUDIO_SAMPLES_PER_SECOND,
    MF_MT_AUDIO_NUM_CHANNELS, MF_MT_AUDIO_BITS_PER_SAMPLE, MF_PD_DURATION, MF_BYTESTREAM_CONTENT_TYPE, MF_BYTESTREAM_ORIGIN_NAME;
extern const GUID MFMediaType_Video, MFMediaType_Audio, MFVideoFormat_NV12, MFAudioFormat_PCM;

IStream *SHCreateMemStream(const BYTE *data, UINT size);    /* copies the bytes */
ULONG IStream_Release(IStream *s);
HRESULT MFCreateMFByteStreamOnStream(IStream *s, IMFByteStream **out);
HRESULT IMFByteStream_QueryInterface(IMFByteStream *b, const GUID *iid, void **out);
ULONG IMFByteStream_Release(IMFByteStream *b);
HRESULT IMFAttributes_SetString(IMFAttributes *a, const GUID *key, LPCWSTR value);
ULONG IMFAttributes_Release(IMFAttributes *a);
HRESULT MFCreateSourceReaderFromByteStream(IMFByteStream *b, IMFAttributes *attrs, IMFSourceReader **out);
ULONG IMFSourceReader_Release(IMFSourceReader *r);
HRESULT IMFSourceReader_GetPresentationAttribute(IMFSourceReader *r, DWORD stream, const GUID *key, PROPVARIANT *out);
HRESULT IMFSourceReader_SetStreamSelection(IMFSourceReader *r, DWORD stream, BOOL on);
HRESULT IMFSourceReader_GetNativeMediaType(IMFSourceReader *r, DWORD stream, DWORD index, IMFMediaType **out);
HRESULT IMFSourceReader_GetCurrentMediaType(IMFSourceReader *r, DWORD stream, IMFMediaType **out);
HRESULT IMFSourceReader_SetCurrentMediaType(IMFSourceReader *r, DWORD stream, DWORD *reserved, IMFMediaType *t);
HRESULT IMFSourceReader_SetCurrentPosition(IMFSourceReader *r, const GUID *format, const PROPVARIANT *pos);
HRESULT IMFSourceReader_ReadSample(IMFSourceReader *r, DWORD stream, DWORD control, DWORD *actual, DWORD *flags, LONGLONG *time, IMFSample **out);
HRESULT MFCreateMediaType(IMFMediaType **out);
ULONG IMFMediaType_Release(IMFMediaType *t);
HRESULT IMFMediaType_SetGUID(IMFMediaType *t, const GUID *key, const GUID *v);
HRESULT IMFMediaType_GetGUID(IMFMediaType *t, const GUID *key, GUID *v);
HRESULT IMFMediaType_SetUINT32(IMFMediaType *t, const GUID *key, UINT32 v);
HRESULT IMFMediaType_GetUINT32(IMFMediaType *t, const GUID *key, UINT32 *v);
HRESULT IMFMediaType_SetUINT64(IMFMediaType *t, const GUID *key, UINT64 v);
HRESULT IMFMediaType_GetUINT64(IMFMediaType *t, const GUID *key, UINT64 *v);
ULONG IMFSample_Release(IMFSample *s);
HRESULT IMFSample_ConvertToContiguousBuffer(IMFSample *s, IMFMediaBuffer **out);
HRESULT IMFMediaBuffer_Lock(IMFMediaBuffer *b, BYTE **data, DWORD *max, DWORD *len);
HRESULT IMFMediaBuffer_Unlock(IMFMediaBuffer *b);
ULONG IMFMediaBuffer_Release(IMFMediaBuffer *b);
HRESULT IMFMediaBuffer_QueryInterface(IMFMediaBuffer *b, const GUID *iid, void **out);   /* no IMF2DBuffer: frames are tightly packed */
HRESULT IMF2DBuffer_Lock2D(IMF2DBuffer *b, BYTE **scan0, LONG *pitch);
HRESULT IMF2DBuffer_Unlock2D(IMF2DBuffer *b);
ULONG IMF2DBuffer_Release(IMF2DBuffer *b);
#endif
