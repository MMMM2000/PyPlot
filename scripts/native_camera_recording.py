"""Experimental native MJPEG archive + bounded, sampled live preview.

No power-supply control. Optional tools are supplied explicitly or with
uv run --no-sync --with opencv-python --with imageio-ffmpeg python ...
Camera mode requires --camera; file input is useful for isolated tests.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        block = stream.read(remaining)
        if not block:
            raise ValueError('Truncated JPEG payload')
        chunks.append(block)
        remaining -= len(block)
    return b''.join(chunks)


def jpeg_packets(stream: BinaryIO):
    """Parse FFmpeg mpjpeg using Content-Length, not embedded JPEG markers."""
    while True:
        line = stream.readline(4096)
        if not line:
            return
        if not line.strip():
            continue
        if not line.startswith(b'--') or not line.endswith(b'\n'):
            raise ValueError('Invalid multipart boundary')
        headers = {}
        while True:
            line = stream.readline(4096)
            # FFmpeg's mpjpeg muxer ends with a boundary followed by EOF.
            # Archive/frame-count validation still detects missing payloads.
            if not line and not headers:
                return
            if not line or not line.endswith(b'\n'):
                raise ValueError('Truncated multipart header')
            if not line.strip():
                break
            key, value = line.split(b':', 1)
            headers[key.strip().lower()] = value.strip().lower()
        if headers.get(b'content-type') != b'image/jpeg':
            raise ValueError('Unexpected multipart content type')
        size = int(headers.get(b'content-length', b'0'))
        if not 0 < size <= 32 * 1024 * 1024:
            raise ValueError('Invalid JPEG packet size')
        yield read_exact(stream, size)


def replace_latest(target: queue.Queue, item) -> None:
    """Preview is intentionally lossy; the native archive never uses this queue."""
    try:
        target.put_nowait(item)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        target.put_nowait(item)


def record(exe: str, output: Path, *, mode: str, seconds: float,
           preview_hz: float, camera: str | None, input_file: Path | None) -> dict:
    import cv2
    import numpy as np
    cv2.setNumThreads(2)
    output.mkdir(parents=True, exist_ok=False)
    size, rate = ('3840x2160', 30) if mode == '4k30' else ('1920x1080', 120)
    command = [exe, '-hide_banner', '-nostdin']
    if camera:
        command += ['-f', 'dshow', '-rtbufsize', '16M', '-video_size', size,
                    '-framerate', str(rate), '-vcodec', 'mjpeg', '-i', 'video=' + camera]
    else:
        command += ['-re', '-i', str(input_file)]
    # The same native packets go to the archive and lightweight preview transport.
    command += ['-map', '0:v:0', '-t', str(seconds), '-an', '-c:v', 'copy',
                str(output / 'native.mkv'), '-map', '0:v:0', '-t', str(seconds),
                '-an', '-c:v', 'copy', '-f', 'mpjpeg', 'pipe:1']
    latest = queue.Queue(maxsize=1)
    stop = threading.Event()
    errors = []
    preview_count = 0
    preview_times = []

    def preview():
        nonlocal preview_count
        last = -math.inf
        try:
            while not stop.is_set() or not latest.empty():
                try:
                    stamp, payload = latest.get(timeout=.1)
                except queue.Empty:
                    continue
                if stamp - last < 1 / preview_hz:
                    continue
                image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    raise ValueError('Preview JPEG cannot be decoded')
                if not preview_count:
                    cv2.imwrite(str(output / 'reference.png'), image)
                preview_count += 1
                preview_times.append({'arrival_monotonic_s': stamp, 'decoded_monotonic_s': time.monotonic()})
                height, width = image.shape
                small = cv2.resize(image, (960, max(1, round(height * 960 / width))))
                cv2.imwrite(str(output / 'latest-preview.jpg'), small)
                last = stamp
        except BaseException as exc:
            errors.append(repr(exc))

    manifest = {'status': 'starting', 'mode': mode, 'requested_fps': rate,
                'preview_target_hz': preview_hz, 'preview_queue_limit': 1,
                'psu_control': False,
                'timing_caveat': 'Host packet arrivals and native PTS; no calibrated exposure-time alignment.'}
    def save():
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    save()
    process = None
    thread = threading.Thread(target=preview, daemon=True)
    frames = 0
    start = time.monotonic()
    timed_out = threading.Event()
    try:
        with (output / 'capture.log').open('wb') as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
            def timeout():
                if process.poll() is None:
                    timed_out.set()
                    process.kill()
            watchdog = threading.Timer(seconds + 20, timeout)
            watchdog.start()
            thread.start()
            try:
                with (output / 'packet-arrivals.csv').open('w', newline='') as handle:
                    writer = csv.writer(handle)
                    writer.writerow(['frame', 'arrival_monotonic_s', 'jpeg_bytes'])
                    for payload in jpeg_packets(process.stdout):
                        if errors:
                            raise RuntimeError(str(errors))
                        stamp = time.monotonic()
                        writer.writerow([frames, stamp, len(payload)])
                        replace_latest(latest, (stamp, payload))
                        frames += 1
                code = process.wait(timeout=5)
                if code or timed_out.is_set():
                    raise RuntimeError(f'Capture failed ({code}); see capture.log')
            finally:
                watchdog.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
        stop.set()
        thread.join(10)
        if thread.is_alive() or errors:
            raise RuntimeError('Preview failed to shut down cleanly: ' + str(errors))
        if not frames or not preview_count:
            raise RuntimeError('No camera frames or decoded previews received')
        warnings = [line for line in (output / 'capture.log').read_text(errors='replace').splitlines()
                    if 'too full' in line.lower() or 'dropp' in line.lower()]
        if warnings:
            raise RuntimeError('Capture reported dropped data: ' + str(warnings))
        manifest.update(status='complete',packet_arrivals=frames,decoded_live_previews=preview_count,
                        wall_seconds=time.monotonic()-start)
        return manifest
    except BaseException as exc:
        manifest.update(status='failed',error=repr(exc))
        raise
    finally:
        stop.set()
        if thread.ident:
            thread.join(10)
        (output / 'preview-timing.json').write_text(json.dumps(preview_times, indent=2))
        save()


def analyze(exe: str, output: Path, roi: tuple[int, int, int, int] | None = None) -> dict:
    """Decode EVERY archived frame, retaining PTS and optional displacement in pixels."""
    import cv2
    import numpy as np
    cv2.setNumThreads(2)
    packet_hash = subprocess.run([exe, '-hide_banner', '-i', str(output/'native.mkv'),
        '-map', '0:v:0', '-c:v', 'copy', '-f', 'framehash', '-'], capture_output=True, text=True, timeout=60)
    if packet_hash.returncode:
        raise RuntimeError(packet_hash.stderr)
    (output / 'native-packets.txt').write_text(packet_hash.stdout)
    lines = packet_hash.stdout.splitlines()
    numerator, denominator = next(x for x in lines if x.startswith('#tb 0:')).split(':')[1].strip().split('/')
    timebase = int(numerator) / int(denominator)
    packets = [x.split(',') for x in lines if x and not x.startswith('#')]
    pts = np.array([float(x[2]) * timebase for x in packets])
    if len(pts) < 2 or np.any(np.diff(pts) <= 0):
        raise ValueError('Missing or non-monotonic packet timestamps')
    manifest = json.loads((output / 'manifest.json').read_text())
    if len(pts) != manifest['packet_arrivals']:
        raise ValueError('Archive/arrival frame counts differ')
    cap = cv2.VideoCapture(str(output / 'native.mkv'))
    count = 0
    template = None
    try:
        with (output / 'all-frame-analysis.csv').open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['frame', 'native_pts_s', 'mean_intensity', 'dx_px', 'dy_px', 'correlation'])
            for i, timestamp in enumerate(pts):
                good, image = cap.read()
                if not good:
                    raise ValueError(f'Cannot decode archive frame {i}')
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                dx = dy = score = None
                if roi:
                    x,y,w,h = roi
                    if min(x,y) < 0 or min(w,h) <= 0 or x+w>gray.shape[1] or y+h>gray.shape[0]:
                        raise ValueError('Tracking ROI outside frame')
                    if template is None:
                        template = gray[y:y+h,x:x+w].copy()
                    radius = max(60, gray.shape[1]//32)
                    x0=max(0,x-radius);y0=max(0,y-radius)
                    region=gray[y0:min(gray.shape[0],y+h+radius),x0:min(gray.shape[1],x+w+radius)]
                    matched=cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED)
                    _,score,_,point=cv2.minMaxLoc(matched)
                    dx=x0+point[0]-x;dy=y0+point[1]-y
                writer.writerow([i,timestamp,float(gray.mean()),dx,dy,score])
                count += 1
            good,_=cap.read()
            if good:
                raise ValueError('Extra decoded frames without recorded PTS')
    finally:
        cap.release()
    hashes=[x[-1].strip() for x in packets]
    summary={'frames_decoded_offline':count,'native_timestamp_fps':float(1/np.diff(pts).mean()),
             'max_packet_gap_ms':float(np.diff(pts).max()*1000),
             'duplicate_encoded_payloads':len(hashes)-len(set(hashes)),
             'decoded_live_previews':manifest['decoded_live_previews'],
             'all_frame_counts_match':True,'tracking_units':'pixels, not calibrated mm',
             'timing_caveat':manifest['timing_caveat']}
    (output / 'analysis-summary.json').write_text(json.dumps(summary,indent=2))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['1080p120','4k30'],default='1080p120')
    parser.add_argument('--seconds',type=float,default=12)
    parser.add_argument('--preview-hz',type=float,default=10)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--analyze-only',action='store_true')
    parser.add_argument('--roi',type=int,nargs=4)
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--camera',help='Exact DirectShow camera name; no power supply is controlled')
    source.add_argument('--input',type=Path)
    args=parser.parse_args()
    if not args.analyze_only and not (args.camera or args.input):
        parser.error('Choose --camera explicitly, or --input for a software test')
    if not math.isfinite(args.seconds) or not 0<args.seconds<=600:
        parser.error('--seconds must be finite and in (0, 600]')
    if not math.isfinite(args.preview_hz) or not 0<args.preview_hz<=30:
        parser.error('--preview-hz must be finite and in (0, 30]')
    if args.ffmpeg:
        exe=args.ffmpeg
    else:
        import imageio_ffmpeg
        exe=imageio_ffmpeg.get_ffmpeg_exe()
    if not args.analyze_only:
        print(json.dumps(record(exe,args.output,mode=args.mode,seconds=args.seconds,
              preview_hz=args.preview_hz,camera=args.camera,input_file=args.input),indent=2),flush=True)
    print(json.dumps(analyze(exe,args.output,tuple(args.roi) if args.roi else None),indent=2),flush=True)


if __name__ == '__main__':
    main()
