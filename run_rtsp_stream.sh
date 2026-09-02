#!/bin/bash

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
VIDEO_PATH="${DIR}/test_video_720p.mp4"

if [ ! -f "$VIDEO_PATH" ]; then
    echo "Error: Video file not found at $VIDEO_PATH"
    exit 1
fi

PORT=8554
STREAM_PATH="live"

# Get local IP addresses to show the user how to connect
IPS=$(hostname -I 2>/dev/null || ip route get 1 2>/dev/null | awk '{print $7}')
PRIMARY_IP=$(echo "$IPS" | awk '{print $1}')

echo "=========================================================="
echo " Starting RTSP Stream using VLC"
echo " Video: $VIDEO_PATH"
echo " Port:  $PORT"
echo " Path:  /$STREAM_PATH"
echo "=========================================================="
echo ""
echo "Stream URLs:"
echo " - Localhost:      rtsp://localhost:${PORT}/${STREAM_PATH}"
if [ -n "$PRIMARY_IP" ]; then
    for IP in $IPS; do
        echo " - Network machine: rtsp://${IP}:${PORT}/${STREAM_PATH}"
    done
fi
echo ""
echo "To stop the stream, press Ctrl+C or run 'killall vlc'."
echo "=========================================================="

# Run cvlc to stream the video on loop
# Flags explanation:
#   -I dummy: run without GUI
#   --loop: loop the video infinitely
#   :sout: configure stream output to stream over RTP/RTSP
#   :sout-all: stream all video/audio tracks
#   :sout-keep: keep the stream alive when looping/reopening input
exec cvlc -I dummy --loop "$VIDEO_PATH" :sout="#rtp{sdp=rtsp://:${PORT}/${STREAM_PATH}}" :sout-all :sout-keep
