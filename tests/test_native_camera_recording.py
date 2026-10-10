import io
import queue

import pytest

from scripts.native_camera_recording import jpeg_packets, replace_latest


def packet(payload):
    return b'--ffmpeg\r\nContent-type: image/jpeg\r\nContent-length: '+str(len(payload)).encode()+b'\r\n\r\n'+payload+b'\r\n'


def test_multipart_preserves_embedded_jpeg_markers():
    payload=b'\xff\xd8thumbnail\xff\xd9actual-image\xff\xd9'
    assert list(jpeg_packets(io.BytesIO(packet(payload)+packet(b'second'))))==[payload,b'second']


def test_ffmpeg_trailing_boundary():
    assert list(jpeg_packets(io.BytesIO(packet(b'jpeg')+b'--ffmpeg\r\n')))==[b'jpeg']


@pytest.mark.parametrize('data',[
    b'bad boundary\n',b'--f\nContent-type: text/plain\nContent-length: 3\n\nabc',
    b'--f\nContent-type: image/jpeg\nContent-length: 4\n\nabc',
    b'--f\nContent-type: image/jpeg\nContent-length: 999999999\n\n',
    b'--f\nContent-type: image/jpeg',
])
def test_invalid_or_truncated_packet_fails(data):
    with pytest.raises(ValueError):
        list(jpeg_packets(io.BytesIO(data)))


def test_preview_queue_is_bounded_and_keeps_latest():
    q=queue.Queue(maxsize=1)
    for n in range(1000):
        replace_latest(q,n)
    assert q.qsize()==1 and q.get_nowait()==999
