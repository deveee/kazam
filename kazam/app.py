# -*- coding: utf-8 -*-
#
#       app.py
#
#       Copyright 2012 David Klasinc <bigwhale@lubica.net>
#       Copyright 2010 Andrew <andrew@karmic-desktop>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 3 of the License, or
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

import os
import locale
import gettext
import logging
import math
logger = logging.getLogger("Main")

from subprocess import Popen
from gi.repository import Gtk, Gdk, GObject
from gettext import gettext as _

#
# Detect GStreamer version and import appropriate functions
#
try:
    from gi.repository.Gst import version as gst_ver
    gst_gi = gst_ver()

    if gst_gi[0]:
        try:
            from kazam.backend.gstreamer_gi import detect_codecs, get_codec
            logger.debug("GStreamer 1.0 or higher detected.")
        except ImportError:
            logger.warning("GStreamer not found in the introspection repository, trying to fallback to pygst.")
            gst_gi = None
    else:
        logger.warning("GStreamer 0.10 detected, falling back to pygst.")
        gst_gi = None
except ImportError:
    logger.warning("GStreamer not found in the introspection repository, trying to fallback to pygst.")
    gst_gi = None

if gst_gi is None:
    try:
        logger.debug("Loading pygst.")
        from kazam.backend.gstreamer import detect_codecs, get_codec
    except ImportError:
        logger.critical("Unable to fallback to pygst, bailing out.")
        Gtk.main_quit()

from kazam.utils import *
from kazam.backend.constants import *
from kazam.backend.config import KazamConfig
from kazam.frontend.about_dialog import AboutDialog
from kazam.frontend.indicator import KazamIndicator
from kazam.frontend.window_region import RegionWindow
from kazam.frontend.done_recording import DoneRecording
from kazam.frontend.window_countdown import CountdownWindow

class KazamApp(GObject.GObject):

    def __init__(self, datadir, dist, debug, test, sound, silent):
        GObject.GObject.__init__(self)
        global gst_gi
        logger.debug("Setting variables.")

        self.startup = True
        self.datadir = datadir
        self.debug = debug
        self.test = test
        self.dist = dist
        self.silent = silent
        self.sound = not sound     # Parameter is called nosound and if true, then we don't have sound.
                                   # Tricky parameters are tricky!
        self.setup_translations()

        if self.sound:
            try:
                from kazam.pulseaudio.pulseaudio import pulseaudio_q
                self.sound = True
            except:
                logger.warning("Pulse Audio Failed to load. Sound recording disabled.")
                self.sound = False

        self.icons = Gtk.IconTheme.get_default()
        self.icons.append_search_path(os.path.join(datadir,"icons", "48x48", "apps"))
        self.icons.append_search_path(os.path.join(datadir,"icons", "16x16", "apps"))

        # Initialize all the variables

        self.audio_source = 0
        self.audio2_source = 0
        self.framerate = 0
        self.counter = 0
        self.codec = 0
        self.main_x = 0
        self.main_y = 0
        self.countdown = None
        self.tempfile = ""
        self.recorder = None
        self.cursor = True
        self.area_window = None
        self.area = None
        self.old_path = None
        self.countdown_splash = True
        self.in_countdown = False
        self.recording_paused = False
        self.recording = False
        self.advanced = False
        self.gst_gi = gst_gi
        self.main_mode = None
        self.record_mode = None
        self.last_mode = None

        if self.sound:
            self.pa_q = pulseaudio_q()
            self.pa_q.start()

        #
        # Setup config
        #
        self.config = KazamConfig()

        self.read_config()

        logger.debug("Connecting indicator signals.")
        logger.debug("Starting in silent mode: {0}".format(self.silent))
        self.indicator = KazamIndicator(self.silent)
        self.indicator.connect("indicator-quit-request", self.cb_quit_request)
        self.indicator.connect("indicator-show-request", self.cb_show_request)
        self.indicator.connect("indicator-start-request", self.cb_start_request)
        self.indicator.connect("indicator-stop-request", self.cb_stop_request)
        self.indicator.connect("indicator-pause-request", self.cb_pause_request)
        self.indicator.connect("indicator-unpause-request", self.cb_unpause_request)
        self.indicator.connect("indicator-about-request", self.cb_about_request)

        #
        # Setup UI
        #
        logger.debug("Main Window UI setup.")

        self.builder = Gtk.Builder()
        self.builder.add_from_file(os.path.join(self.datadir, "ui", "kazam.ui"))
        self.builder.connect_signals(self)
        for w in self.builder.get_objects():
            if issubclass(type(w), Gtk.Buildable):
                name = Gtk.Buildable.get_name(w)
                setattr(self, name, w)
            else:
                logger.debug("Unable to get name for '%s'" % w)

        #
        # If these are added in glade, something weird happens - investigate! :)
        #
        self.volume_adjustment = Gtk.Adjustment(0, 0, 60, 1, 3, 0)
        self.volume2_adjustment = Gtk.Adjustment(0, 0, 60, 1, 3, 0)
        self.framerate_adjustment = Gtk.Adjustment(25, 1, 60, 1, 5, 0)
        self.counter_adjustment = Gtk.Adjustment(5, 0, 65, 1, 5, 0)
        #self.volumebutton_audio.set_adjustment(self.volume_adjustment)
        #self.volumebutton_audio2.set_adjustment(self.volume2_adjustment)
        #self.spinbutton_framerate.set_adjustment(self.framerate_adjustment)
        #self.spinbutton_counter.set_adjustment(self.counter_adjustment)
        #
        #
        #

        #renderer_text = Gtk.CellRendererText()
        #self.combobox_codec.pack_start(renderer_text, True)
        #self.combobox_codec.add_attribute(renderer_text, "text", 1)

        self.context = self.toolbar_main.get_style_context()
        self.context.add_class(Gtk.STYLE_CLASS_PRIMARY_TOOLBAR)

        self.btn_cast = Gtk.RadioToolButton(group=None)
        self.btn_cast.set_label("Screencast")
        img1 = Gtk.Image.new_from_file(os.path.join(self.datadir, "icons", "light", "screencast.png"))
        self.btn_cast.set_icon_widget(img1)
        self.btn_cast.set_active(True)
        self.btn_cast.set_name("MAIN_SCREENCAST")
        self.btn_cast.connect("toggled", self.cb_main_toggled)

        self.btn_shot = Gtk.RadioToolButton(group=self.btn_cast)
        self.btn_shot.set_label("Screenshot")
        img2 = Gtk.Image.new_from_file(os.path.join(self.datadir, "icons", "light", "screenshot-1.png"))
        self.btn_shot.set_icon_widget(img2)
        self.btn_shot.set_name("MAIN_SCREENSHOT")
        self.btn_shot.connect("toggled", self.cb_main_toggled)


        self.sep_1 = Gtk.SeparatorToolItem()
        self.sep_1.set_draw(False)
        self.sep_1.set_expand(True)
        self.toolbar_main.insert(self.sep_1, -1)
        self.toolbar_main.insert(self.btn_cast, -1)
        self.toolbar_main.insert(self.btn_shot, -1)
        self.toolbar_main.insert(self.sep_1, -1)


        # Auxiliary toolbar
        self.btn_full = Gtk.RadioToolButton(group=None)
        self.btn_full.set_label("Fullscreen")
        img3 = Gtk.Image.new_from_file(os.path.join(self.datadir, "icons", "dark", "fullscreen.png"))
        self.btn_full.set_icon_widget(img3)
        self.btn_full.set_active(True)
        self.btn_full.set_name("MODE_FULL")
        self.btn_full.connect("toggled", self.cb_record_mode_toggled)

        self.btn_allscreens = Gtk.RadioToolButton(group=self.btn_full)
        self.btn_allscreens.set_label("All Screens")
        img4 = Gtk.Image.new_from_file(os.path.join(self.datadir, "icons", "dark", "all-screens.png"))
        self.btn_allscreens.set_icon_widget(img4)
        self.btn_allscreens.set_name("MODE_ALL")
        self.btn_allscreens.connect("toggled", self.cb_record_mode_toggled)

        self.btn_area = Gtk.RadioToolButton(group=self.btn_full)
        self.btn_area.set_label("Area")
        img5 = Gtk.Image.new_from_file(os.path.join(self.datadir, "icons", "dark", "area.png"))
        self.btn_area.set_icon_widget(img5)
        self.btn_area.set_name("MODE_AREA")
        self.btn_area.connect("toggled", self.cb_record_mode_toggled)

        self.sep_2 = Gtk.SeparatorToolItem()
        self.sep_2.set_draw(False)
        self.sep_2.set_expand(True)
        self.toolbar_aux.insert(self.sep_2, -1)
        self.toolbar_aux.insert(self.btn_full, -1)
        self.toolbar_aux.insert(self.btn_allscreens, -1)
        self.toolbar_aux.insert(self.btn_area, -1)
        self.toolbar_aux.insert(self.sep_2, -1)

        #
        # Take care of screen size changes.
        #
        self.default_screen = Gdk.Screen.get_default()
        self.default_screen.connect("size-changed", self.cb_screen_size_changed)
        self.window.connect("configure-event", self.cb_configure_event)

        # Fetch sources info, take care of all the widgets and saved settings and show main window
        if self.sound:
            self.get_audio_sources()
            self.populate_widgets()

        if not self.silent:
            self.window.show_all()
        else:
            logger.info("Starting in silent mode:\n  SUPER-CTRL-W to toggle main window.\n  SUPER-CTRL-Q to quit.")

        self.restore_state()

        if not self.sound:
            self.combobox_audio.set_sensitive(False)
            self.combobox_audio2.set_sensitive(False)
            self.volumebutton_audio.set_sensitive(False)
            self.volumebutton_audio2.set_sensitive(False)

        self.startup = False

    #
    # Callbacks, go down here ...
    #

    #
    # Mode of operation toggles
    #
    def cb_main_toggled(self, widget):
        if widget.get_name() == "MAIN_SCREENCAST" and widget.get_active():
            self.main_mode = MODE_SCREENCAST
            self.ntb_main.set_current_page(0)

        if widget.get_name() == "MAIN_SCREENSHOT" and widget.get_active():
            self.main_mode = MODE_SCREENSHOT
            self.ntb_main.set_current_page(1)

    def cb_record_mode_toggled(self, widget):
        if widget.get_active():
            self.current_mode = widget
        else:
            self.last_mode = widget

        if widget.get_name() == "MODE_AREA" and widget.get_active():
            logger.debug("Region ON.")
            self.area_window = RegionWindow(self.area)
            self.tmp_sig1 = self.area_window.connect("area-selected", self.cb_area_selected)
            self.tmp_sig2 = self.area_window.connect("area-canceled", self.cb_area_canceled)
            self.window.set_sensitive(False)
            self.record_mode = MODE_AREA

        if widget.get_name() == "MODE_AREA" and not widget.get_active():
            logger.debug("Region OFF.")
            if self.area_window:
                self.area_window.disconnect(self.tmp_sig1)
                self.area_window.disconnect(self.tmp_sig2)
                self.area_window.window.destroy()
                self.area_window = None

        if widget.get_name() == "MODE_FULL" and widget.get_active():
            self.record_mode = MODE_FULL

        if widget.get_name() == "MODE_ALL" and widget.get_active():
            self.record_mode = MODE_ALL

    def cb_area_selected(self, widget):
        logger.debug("Region selected: {0}, {1}, {2}, {3}".format(
            self.area_window.startx,
            self.area_window.starty,
            self.area_window.endx,
            self.area_window.endy))
        self.window.set_sensitive(True)
        self.area = (self.area_window.startx,
                     self.area_window.starty,
                     self.area_window.endx,
                     self.area_window.endy)

    def cb_area_canceled(self, widget):
        logger.debug("Region selection canceled.")
        self.window.set_sensitive(True)
        self.last_mode.set_active(True)

    def cb_screen_size_changed(self, screen):
        logger.debug("Screen size changed.")
        self.get_sources(audio = False)

    def cb_configure_event(self, widget, event):
        if event.type == Gdk.EventType.CONFIGURE:
            #
            # When you close main window up to 5 configure events are triggered and some of them have X & Y set to 0 ?!?
            #
            if event.x or event.y > 0:
                self.main_x = event.x
                self.main_y = event.y


    def cb_quit_request(self, indicator):
        logger.debug("Quit requested.")
        (self.main_x, self.main_y) = self.window.get_position()
        try:
            os.remove(self.recorder.tempfile)
            os.remove("{0}.mux".format(self.recorder.tempfile))
        except OSError:
            logger.info("Unable to delete one of the temporary files. Check your temporary directory.")
        except AttributeError:
            pass
        self.save_state()
        if self.sound:
            self.pa_q.end()

        Gtk.main_quit()

    def cb_show_request(self, indicator):
        if not self.window.get_property("visible"):
            logger.debug("Show requested, raising window.")
            self.window.show_all()
            self.window.present()
            self.window.move(self.main_x, self.main_y)
        else:
            self.window.hide()

    def cb_close_clicked(self, indicator):
        (self.main_x, self.main_y) = self.window.get_position()
        self.window.hide()

    def cb_about_request(self, activated):
        AboutDialog(self.icons)

    def cb_delete_event(self, widget, user_data):
        self.cb_quit_request(None)

    def cb_audio_changed(self, widget):
        logger.debug("Audio Changed.")

        self.audio_source = self.combobox_audio.get_active()
        logger.debug("  - A_1 {0}".format(self.audio_source))

        if self.audio_source:
            pa_audio_idx =  self.audio_sources[self.audio_source][0]
            self.pa_q.set_source_mute_by_index(pa_audio_idx, 0)

            logger.debug("  - PA Audio1 IDX: {0}".format(pa_audio_idx))
            self.audio_source_info = self.pa_q.get_source_info_by_index(pa_audio_idx)
            if len(self.audio_source_info) > 0:
                val = self.pa_q.cvolume_to_dB(self.audio_source_info[2])
                if math.isinf(val):
                    vol = 0
                else:
                    vol = 60 + val
                self.volumebutton_audio.set_value(vol)
            else:
                logger.debug("Error getting volume info for Audio 1")

            if len(self.audio_source_info):
               logger.debug("New Audio1:\n  {0}".format(self.audio_source_info[3]))
            else:
                logger.debug("New Audio1:\n  Error retrieving data.")

            if self.audio_source and self.audio_source == self.audio2_source:
                if self.audio_source < len(self.audio_sources):
                    self.audio2_source += 1
                else:
                    self.audio2_source = 0
                self.combobox_audio2.set_active(0)

            self.volumebutton_audio.set_sensitive(True)
            self.combobox_audio2.set_sensitive(True)
        else:
            self.volumebutton_audio.set_sensitive(False)
            self.combobox_audio2.set_sensitive(False)
            self.combobox_audio2.set_active(0)
            logger.debug("Audio1 OFF.")

    def cb_audio2_changed(self, widget):
        logger.debug("Audio2 Changed.")

        self.audio2_source = self.combobox_audio2.get_active()
        logger.debug("  - A_2 {0}".format(self.audio2_source))

        if self.audio2_source:
            pa_audio2_idx =  self.audio_sources[self.audio2_source][0]
            self.pa_q.set_source_mute_by_index(pa_audio2_idx, 0)

            logger.debug("  - PA Audio2 IDX: {0}".format(pa_audio2_idx))
            self.audio2_source_info = self.pa_q.get_source_info_by_index(pa_audio2_idx)

            if len(self.audio2_source_info) > 0:
                val = self.pa_q.cvolume_to_dB(self.audio2_source_info[2])
                if math.isinf(val):
                    vol = 0
                else:
                    vol = 60 + val
                self.volumebutton_audio2.set_value(vol)
            else:
                logger.debug("Error getting volume info for Audio 1")

            if len(self.audio2_source_info):
                logger.debug("New Audio2:\n  {0}".format(self.audio2_source_info[3]))
            else:
                logger.debug("New Audio2:\n  Error retrieving data.")

            if self.audio_source and self.audio_source == self.audio2_source:
                if self.audio_source < len(self.audio_sources):
                    self.audio2_source += 1
                else:
                    self.audio2_source = 0

                self.combobox_audio2.set_active(0)
            self.volumebutton_audio2.set_sensitive(True)
        else:
            self.volumebutton_audio2.set_sensitive(False)
            logger.debug("Audio2 OFF.")

    def cb_codec_changed(self, widget):
        i = widget.get_active()
        model = widget.get_model()
        iter = model.get_iter(i)
        self.codec = model.get_value(iter, 0)
        logger.debug('Codec selected: {0} - {1}'.format(get_codec(self.codec)[2], self.codec))

    def cb_start_request(self, widget):
        logger.debug("Start recording selected.")
        self.run_counter()

    def cb_record_clicked(self, widget):
        logger.debug("Record clicked, invoking Screencast.")
        self.run_counter()

    def cb_volume_changed(self, widget, value):
        logger.debug("Volume 1 changed, new value: {0}".format(value))
        idx = self.combobox_audio.get_active()
        pa_idx =  self.audio_sources[idx][0]
        chn = self.audio_source_info[2].channels
        cvol = self.pa_q.dB_to_cvolume(chn, value-60)
        self.pa_q.set_source_volume_by_index(pa_idx, cvol)

    def cb_volume2_changed(self, widget, value):
        logger.debug("Volume 2 changed, new value: {0}".format(value))
        idx = self.combobox_audio2.get_active()
        pa_idx =  self.audio_sources[idx][0]
        chn = self.audio_source_info[2].channels
        cvol = self.pa_q.dB_to_cvolume(chn, value-60)
        self.pa_q.set_source_volume_by_index(pa_idx, cvol)

    def cb_counter_finished(self, widget):
        logger.debug("Counter finished.")
        self.in_countdown = False
        self.countdown = None
        self.indicator.menuitem_finish.set_label(_("Finish recording"))
        self.indicator.menuitem_pause.set_sensitive(True)
        self.indicator.blink_set_state(BLINK_STOP)
        self.indicator.start_recording()
        self.recorder.start_recording()

    def cb_stop_request(self, widget):
        self.recording = False
        if self.in_countdown:
            logger.debug("Cancel countdown request.")
            self.countdown.cancel_countdown()
            self.countdown = None
            self.indicator.menuitem_finish.set_label(_("Finish recording"))
            self.window.set_sensitive(True)
            self.window.show()
            self.window.present()
        else:
            if self.recording_paused:
                self.recorder.unpause_recording()
            logger.debug("Stop request.")
            self.recorder.stop_recording()
            self.tempfile = self.recorder.get_tempfile()
            logger.debug("Recorded tmp file: {0}".format(self.tempfile))
            logger.debug("Waiting for data to flush.")

    def cb_flush_done(self, widget):
        self.done_recording = DoneRecording(self.icons,
                                            self.tempfile,
                                            self.codec,
                                            self.old_path)
        logger.debug("Done Recording initialized.")
        self.done_recording.connect("save-done", self.cb_save_done)
        self.done_recording.connect("save-cancel", self.cb_save_cancel)
        self.done_recording.connect("edit-request", self.cb_edit_request)
        logger.debug("Done recording signals connected.")
        self.done_recording.show_all()
        self.window.set_sensitive(False)

    def cb_pause_request(self, widget):
        logger.debug("Pause requested.")
        self.recording_paused = True
        self.recorder.pause_recording()

    def cb_unpause_request(self, widget):
        logger.debug("Unpause requested.")
        self.recording_paused = False
        self.recorder.unpause_recording()

    def cb_save_done(self, widget, result):
        logger.debug("Save Done, result: {0}".format(result))
        self.old_path = result
        self.window.set_sensitive(True)
        self.window.show_all()
        self.window.present()
        self.window.move(self.main_x, self.main_y)

    def cb_save_cancel(self, widget):
        try:
            logger.debug("Save canceled, removing {0}".format(self.tempfile))
            os.remove(self.tempfile)
        except OSError:
            logger.info("Failed to remove tempfile {0}".format(self.tempfile))
        except AttributeError:
            logger.info("Failed to remove tempfile {0}".format(self.tempfile))
            pass

        self.window.set_sensitive(True)
        self.window.show_all()
        self.window.present()
        self.window.move(self.main_x, self.main_y)

    def cb_help_about(self, widget):
        AboutDialog(self.icons)

    def cb_edit_request(self, widget, data):
        (command, arg_list) = data
        arg_list.insert(0, command)
        arg_list.append(self.tempfile)
        logger.debug("Edit request, cmd: {0}".format(arg_list))
        Popen(arg_list)
        self.window.set_sensitive(True)
        self.window.show_all()

    def cb_switch_silent(self, widget, user_data):
        self.silent = not self.silent
        logger.debug("Silent mode: {0}.".format(self.silent))

    def cb_switch_cursor(self, widget, user_data):
        self.cursor = not self.cursor
        logger.debug("Cursor capture: {0}.".format(self.cursor))

    def cb_switch_countdown_splash(self, widget, user_data):
        self.countdown_splash = not self.countdown_splash
        logger.debug("Coutndown splash: {0}.".format(self.countdown_splash))

    def cb_switch_codecs(self, widget, user_data):
        self.advanced = not self.advanced
        self.populate_codecs()
        logger.debug("Advanced codecs: {0}".format(self.advanced))


    #
    # Other somewhat useful stuff ...
    #

    def run_counter(self):
        #
        # Annoyances with the menus
        #
        (main_x, main_y) = self.window.get_position()
        if main_x and main_y:
            self.main_x = main_x
            self.main_y = main_y

        self.indicator.recording = True
        self.indicator.menuitem_start.set_sensitive(False)
        self.indicator.menuitem_pause.set_sensitive(False)
        self.indicator.menuitem_finish.set_sensitive(True)
        self.indicator.menuitem_show.set_sensitive(False)
        self.indicator.menuitem_quit.set_sensitive(False)
        self.indicator.menuitem_finish.set_label(_("Cancel countdown"))
        self.in_countdown = True

        if self.gst_gi:
            from kazam.backend.gstreamer_gi import Screencast
        else:
            from kazam.backend.gstreamer import Screencast

        self.recorder = Screencast(self.debug)
        self.indicator.blink_set_state(BLINK_START)

        if self.sound:
            if self.audio_source > 0:
                audio_source = self.audio_sources[self.audio_source][1]
            else:
                audio_source = None

            if self.audio2_source > 0:
                audio2_source = self.audio_sources[self.audio2_source][1]
            else:
                audio2_source = None
        else:
            audio_source = None
            audio2_source = None

        # if self.video_source is not None:
        #    video_source = self.video_sources[self.video_source]
        #else:
        #    video_source = None

        screen = HW.get_current_screen()

        framerate = self.spinbutton_framerate.get_value_as_int()
        self.recorder.setup_sources(video_source,
                                    audio_source,
                                    audio2_source,
                                    self.codec,
                                    self.cursor,
                                    framerate,
                                    self.area if self.record_mode == MODE_AREA else None,
                                    self.test,
                                    self.dist)

        self.recorder.connect("flush-done", self.cb_flush_done)
        self.countdown = CountdownWindow(self.indicator, show_window = self.countdown_splash)
        self.countdown.connect("counter-finished", self.cb_counter_finished)
        self.countdown.run(self.spinbutton_counter.get_value_as_int())
        self.recording = True
        logger.debug("Hiding main window.")
        self.window.hide()

    def setup_translations(self):
        gettext.bindtextdomain("kazam", "/usr/share/locale")
        gettext.textdomain("kazam")
        try:
            locale.setlocale(locale.LC_ALL, "")
        except Exception as e:
            logger.exception("EXCEPTION: Setlocale failed, no language support.")

    def read_config (self):

        self.audio_source = self.config.getint("main", "audio_source")
        self.audio2_source = self.config.getint("main", "audio2_source")

        self.main_x = self.config.getint("main", "last_x")
        self.main_y = self.config.getint("main", "last_y")

        self.codec = self.config.getint("main", "codec")

        self.counter = self.config.getfloat("main", "counter")
        self.framerate = self.config.getfloat("main", "framerate")

        self.cursor = self.config.getboolean("main", "capture_cursor")
        self.countdown_splash = self.config.getboolean("main", "countdown_splash")
        self.advanced = self.config.getboolean("main", "advanced")
        self.silent = self.config.getboolean("main", "silent")

    def restore_state (self):

        logger.debug("Restoring state - sources: A_1 ({0}), A_2 ({1})".format(self.audio_source,
                                                                              self.audio2_source))
        self.window.move(self.main_x, self.main_y)

        if self.sound:
            logger.debug("Getting volume info.")

            #self.combobox_audio.set_active(self.audio_source)
            #self.combobox_audio2.set_active(self.audio2_source)

            #if self.audio_source:
            #    self.volumebutton_audio.set_sensitive(True)
            #    self.combobox_audio2.set_sensitive(True)
            #else:
            #    self.volumebutton_audio.set_sensitive(False)
            #    self.combobox_audio2.set_sensitive(False)

            #if self.audio2_source:
            #    self.volumebutton_audio2.set_sensitive(True)
            #else:
            #    self.volumebutton_audio2.set_sensitive(False)

        #if self.advanced:
        #    self.switch_codecs.set_active(True)
        #    self.advanced = True
        #    self.populate_codecs()
        #if self.cursor:
        #    self.switch_cursor.set_active(True)
        #    self.cursor = True
        #if self.countdown_splash:
        #    self.switch_countdown_splash.set_active(True)
        #    self.countdown_splash = True
        #if self.silent:
        #    self.switch_silent.set_active(True)
        #    self.silent = True

        #self.spinbutton_counter.set_value(self.counter)
        #self.spinbutton_framerate.set_value(self.framerate)

        #codec_model = self.combobox_codec.get_model()

        #
        # Crappy code below ...
        #
        #cnt = 0
        #bingo = False
        #for entry in codec_model:
        #    if self.codec == entry[0]:
        #        bingo = True
        #        break
        #    cnt += 1
        #if not bingo:
        #    cnt = 0

        #
        # No, I wasn't kidding ...
        #

        #codec_iter = codec_model.get_iter(cnt)
        #self.combobox_codec.set_active_iter(codec_iter)
        #self.codec = codec_model.get_value(codec_iter, 0)

    def save_state(self):
        logger.debug("Saving state.")

        #if self.sound:
        #    audio_source = self.combobox_audio.get_active()
        #    audio2_source = self.combobox_audio2.get_active()
        #    self.config.set("main", "audio_source", audio_source)
        #    self.config.set("main", "audio2_source", audio2_source)

        #self.config.set("main", "capture_cursor", self.cursor)
        #self.config.set("main", "countdown_splash", self.countdown_splash)

        #self.config.set("main", "last_x", self.main_x)
        #self.config.set("main", "last_y", self.main_y)


        #codec = self.combobox_codec.get_active()
        #codec_model = self.combobox_codec.get_model()
        #codec_model_iter = codec_model.get_iter(codec)
        #codec_value = codec_model.get_value(codec_model_iter, 0)
        #self.config.set("main", "codec", codec_value)

        #self.config.set("main", "advanced", self.advanced)

        #counter = int(self.spinbutton_counter.get_value())
        #self.config.set("main", "counter", counter)

        #framerate = int(self.spinbutton_framerate.get_value())
        #self.config.set("main", "framerate", framerate)

        #self.config.set("main", "silent", self.silent)

        #self.config.write()

    def get_audio_sources(self):
        logger.debug("Getting Audio sources.")
        try:
            self.audio_sources = self.pa_q.get_audio_sources()
            self.audio_sources.insert(0, [])
            if self.debug:
                for src in self.audio_sources:
                    logger.debug(" Device found: ")
                    for item in src:
                        logger.debug("  - {0}".format(item))
        except:
            # Something went wrong, just fallback to no-sound
            logger.warning("Unable to find any audio devices.")
            self.audio_sources = [[0, _("Unknown"), _("Unknown")]]

    #
    # TODO: Merge with get_sources?
    #
    def populate_widgets(self):
        pass
        #for source in self.audio_sources:
        #    if not len(source):
        #        self.combobox_audio.append(None, "Off")
        #        self.combobox_audio2.append(None, "Off")
        #    else:
        #        self.combobox_audio.append(None, source[2])
        #        self.combobox_audio2.append(None, source[2])

        #self.populate_codecs()


    def populate_codecs(self):
        #old_model = self.combobox_codec.get_model()
        #old_model = None

        #codec_model = Gtk.ListStore(int, str)

        #codecs = detect_codecs()

        #for codec in codecs:
        #    if CODEC_LIST[codec][4] and self.advanced:
        #        codec_model.append([CODEC_LIST[codec][0], CODEC_LIST[codec][2]])
        #    elif not CODEC_LIST[codec][4]:
        #        codec_model.append([CODEC_LIST[codec][0], CODEC_LIST[codec][2]])

        #self.combobox_codec.set_model(codec_model)

        #if not self.startup:
        #    self.combobox_codec.set_active(0)
        pass


