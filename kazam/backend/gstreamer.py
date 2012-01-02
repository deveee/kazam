#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#       gstreamer.py
#       
#       Copyright 2010 David Klasinc <bigwhale@lubica.net>
#       
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#       
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#       
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.

from subprocess import Popen
import tempfile
import os
import gobject
import glib
import signal
import multiprocessing
import pygst
pygst.require("0.10")
import gst

from kazam.backend.constants import *


class Screencast(object):
    def __init__(self):
        self.tempfile = tempfile.mktemp(prefix="kazam_", suffix=".mkv")
        self.pipeline = gst.Pipeline("Kazam")
        
    def setup_sources(self, video_source, audio_source, codec):
        
        # Get the number of cores available then use all except one for encoding
        cores = multiprocessing.cpu_count()

        if cores > 1:
            cores = cores - 1

        self.audio_source = audio_source
        x = video_source.x
        y = video_source.y
        width = video_source.width
        height = video_source.height
        endx = x + width - 1
        endy = y + height - 1
        display = video_source.display
        self.videosrc = gst.element_factory_make("ximagesrc", "video_src")
        self.videosrc.set_property("startx", x)
        self.videosrc.set_property("starty", y)
        self.videosrc.set_property("endx", endx)
        self.videosrc.set_property("endy", endy)
        self.videosrc.set_property("use-damage", False)
        self.videosrc.set_property("show-pointer", True)   # This should be made customizable
  
        self.videorate = gst.element_factory_make("videorate", "video_rate")
        self.vid_caps = gst.Caps("video/x-raw-rgb, framerate=25/1")  # This also ...
        self.vid_caps_filter = gst.element_factory_make("capsfilter", "vid_filter")
        self.vid_caps_filter.set_property("caps", self.vid_caps)
        self.ffmpegcolor = gst.element_factory_make("ffmpegcolorspace", "ffmpeg")

        if codec == CODEC_VP8:
            self.videnc = gst.element_factory_make("vp8enc", "video_encoder")
            self.videnc.set_property("speed", 2)
            self.videnc.set_property("quality", 10)
            self.videnc.set_property("threads", cores)
        elif codec == CODEC_H264:
            self.videnc = gst.element_factory_make("x264enc", "video_encoder")
            self.videnc.set_property("key-int-max", 10)
            self.videnc.set_property("bframes", 4)
            self.videnc.set_property("pass", 4)
            self.videnc.set_property("cabac", 0)
            self.videnc.set_property("me", "dia")
            self.videnc.set_property("subme", 1)
            self.videnc.set_property("qp-min", 1)
            self.videnc.set_property("qp-max", 51)
            self.videnc.set_property("qp-step", 4)
            self.videnc.set_property("quantizer", 1)


        self.sink = gst.element_factory_make("filesink", "sink")
        self.sink.set_property("location", self.tempfile)
        if codec == CODEC_VP8:
            self.mux = gst.element_factory_make("webmmux", "muxer")
        elif codec == CODEC_H264:
            self.mux = gst.element_factory_make("matroskamux", "muxer")

        self.vid_in_queue = gst.element_factory_make("queue", "queue_v1")
        self.vid_out_queue = gst.element_factory_make("queue", "queue_v2")

        self.file_queue = gst.element_factory_make("queue", "queue_file")

        if self.audio_source:
            self.audiosrc = gst.element_factory_make("pulsesrc", "audio_src")
            self.audiosrc.set_property("device", self.audio_source)
            self.aud_caps = gst.Caps("audio/x-raw-int")
            self.aud_caps_filter = gst.element_factory_make("capsfilter", "aud_filter")
            self.aud_caps_filter.set_property("caps", self.aud_caps)
            self.audioconv = gst.element_factory_make("audioconvert", "audio_conv")
            if codec == CODEC_VP8:
                self.audioenc = gst.element_factory_make("vorbisenc", "audio_encoder")
                self.audioenc.set_property("quality", 1)
            elif codec == CODEC_H264:
                self.audioenc = gst.element_factory_make("flacenc", "audio_encoder")

            self.aud_in_queue = gst.element_factory_make("queue", "queue_a1")
            self.aud_out_queue = gst.element_factory_make("queue", "queue_a2")

            self.pipeline.add(self.videosrc, self.vid_in_queue, self.videorate,
                              self.vid_caps_filter, self.ffmpegcolor,
                              self.videnc, self.audiosrc, self.aud_in_queue,
                              self.aud_caps_filter, self.vid_out_queue,
                              self.aud_out_queue, self.audioconv,
                              self.audioenc, self.mux, self.file_queue, self.sink)

            gst.element_link_many(self.videosrc, self.vid_in_queue,
                                  self.videorate, self.vid_caps_filter,
                                  self.ffmpegcolor, self.videnc,
                                  self.vid_out_queue, self.mux)

            gst.element_link_many(self.audiosrc, self.aud_in_queue,
                                  self.aud_caps_filter, 
                                  self.audioconv, self.audioenc,
                                  self.aud_out_queue, self.mux)

            gst.element_link_many(self.mux, self.file_queue, self.sink)
        else:
            self.pipeline.add(self.videosrc, self.vid_in_queue, self.videorate,
                              self.vid_caps_filter, self.ffmpegcolor,
                              self.videnc, self.vid_out_queue, self.mux,
                              self.sink)

            gst.element_link_many(self.videosrc, self.vid_in_queue,
                                  self.videorate, self.vid_caps_filter,
                                  self.ffmpegcolor, self.videnc,
                                  self.vid_out_queue, self.mux, self.sink)

    def start_recording(self):
        self.pipeline.set_state(gst.STATE_PLAYING)

    def pause_recording(self):
        self.pipeline.set_state(gst.STATE_PAUSED)
        
    def unpause_recording(self):
        self.pipeline.set_state(gst.STATE_PLAYING)
    
    def stop_recording(self):
        self.pipeline.set_state(gst.STATE_NULL)
        
    def get_recording_filename(self):
        return self.tempfile
        
    def get_audio_recorded(self):
        return self.audio
        
    def convert(self, options, converted_file_extension, video_quality,
                    audio_quality=None):
                        
        self.converted_file_extension = converted_file_extension
        
        # Create our ffmpeg arguments list
        args_list = ["ffmpeg"]
        # Add the input file
        args_list += ["-i", self.tempfile]
        # Add any UploadSource specific options
        args_list += options
        
        # Configure the quality as selected by the user
        # If the quality slider circle is at the right-most position
        # use the same quality option
        if video_quality == 6001:
            args_list += ["-sameq"]
        else:
            args_list += ["-b", "%sk" % video_quality]
        if audio_quality:
            args_list += ["-ab", "%sk" % audio_quality]
        # Finally add the desired output file
        args_list += ["%s%s" %(self.tempfile[:-4], converted_file_extension)]
        
        # Run the ffmpeg command and when it is done, set a variable to 
        # show we have finished
        command = Popen(args_list)
        glib.timeout_add(100, self._poll, command)
        
    def _poll(self, command):
        ret = command.poll()
        if ret is None:
            # Keep monitoring
            return True
        else:
            self.converted_file = "%s%s" %(self.tempfile[:-4], self.converted_file_extension)
            return False
 
