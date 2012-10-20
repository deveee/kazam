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
import math
import locale
import gettext
import logging

from subprocess import Popen
from gi.repository import Gtk, Gdk, GObject
from gettext import gettext as _

from kazam.utils import *
from kazam.backend.prefs import *
from kazam.backend.constants import *
from kazam.backend.config import KazamConfig
from kazam.frontend.main_menu import MainMenu
from kazam.backend.gstreamer_gi import Screencast
from kazam.frontend.preferences import Preferences
from kazam.frontend.about_dialog import AboutDialog
from kazam.frontend.indicator import KazamIndicator
from kazam.frontend.window_region import RegionWindow
from kazam.frontend.done_recording import DoneRecording
from kazam.frontend.window_countdown import CountdownWindow

logger = logging.getLogger("Main")

#
# Detect GStreamer version and bail out if lower than 1.0 and no GI
#
try:
    from gi.repository import Gst
    gst_gi = Gst.version()
    if not gst_gi[0]:
        logger.critical("Gstreamer 1.0 or higher requred, bailing out.")
        Gtk.main_quit()
    else:
        logger.debug("Gstreamer version detected: {0}.{1}.{2}.{3}".format(gst_gi[0],
                                                                      gst_gi[1],
                                                                      gst_gi[2],
                                                                      gst_gi[3]))
except ImportError:
    logger.critical("Gstreamer 1.0 or higher requred, bailing out.")
    Gtk.main_quit()

class KazamApp(GObject.GObject):

    def __init__(self, datadir, dist, debug, test, sound, silent):
        GObject.GObject.__init__(self)
        logger.debug("Setting variables.")

        prefs.datadir = datadir

        self.startup = True
        prefs.debug = debug
        prefs.test = test
        prefs.dist = dist
        prefs.silent = silent
        prefs.sound = not sound     # Parameter is called nosound and if true, then we don't have sound.
                                   # Tricky parameters are tricky!
        self.setup_translations()

        if prefs.sound:
            try:
                from kazam.pulseaudio.pulseaudio import pulseaudio_q
                prefs.sound = True
            except:
                logger.warning("Pulse Audio Failed to load. Sound recording disabled.")
                prefs.sound = False

        self.icons = Gtk.IconTheme.get_default()
        self.icons.append_search_path(os.path.join(prefs.datadir,"icons", "48x48", "apps"))
        self.icons.append_search_path(os.path.join(prefs.datadir,"icons", "16x16", "apps"))

        # Initialize all the variables

        self.audio_source = 0
        self.audio2_source = 0
        self.main_x = 0
        self.main_y = 0
        self.countdown = None
        self.tempfile = ""
        self.recorder = None
        self.area_window = None
        self.area = None
        self.old_path = None
        self.in_countdown = False
        self.recording_paused = False
        self.recording = False
        self.main_mode = 0
        self.record_mode = 0
        self.last_mode = None

        if prefs.sound:
            prefs.pa_q = pulseaudio_q()
            prefs.pa_q.start()

        self.mainmenu = MainMenu()

        #
        # Setup config
        #
        self.config = KazamConfig()

        self.read_config()

        logger.debug("Connecting indicator signals.")
        logger.debug("Starting in silent mode: {0}".format(prefs.silent))
        self.indicator = KazamIndicator(prefs.silent)
        self.indicator.connect("indicator-quit-request", self.cb_quit_request)
        self.indicator.connect("indicator-show-request", self.cb_show_request)
        self.indicator.connect("indicator-start-request", self.cb_start_request)
        self.indicator.connect("indicator-stop-request", self.cb_stop_request)
        self.indicator.connect("indicator-pause-request", self.cb_pause_request)
        self.indicator.connect("indicator-unpause-request", self.cb_unpause_request)
        self.indicator.connect("indicator-about-request", self.cb_about_request)

        self.mainmenu.connect("file-quit", self.cb_quit_request)
        self.mainmenu.connect("file-preferences", self.cb_preferences_request)
        self.mainmenu.connect("help-about", self.cb_help_about)

        #
        # Setup UI
        #
        logger.debug("Main Window UI setup.")

        self.builder = Gtk.Builder()
        self.builder.add_from_file(os.path.join(prefs.datadir, "ui", "kazam.ui"))
        self.builder.connect_signals(self)
        # self.adjustment_delay = self.builder.get_object("adjustment_delay")
        for w in self.builder.get_objects():
            if issubclass(type(w), Gtk.Buildable):
                name = Gtk.Buildable.get_name(w)
                setattr(self, name, w)
            else:
                logger.debug("Unable to get name for '%s'" % w)

        # Main Menu
        self.MainGrid.attach(self.mainmenu.menubar, 0, 0, 1, 1)

        self.context = self.toolbar_main.get_style_context()
        self.context.add_class(Gtk.STYLE_CLASS_PRIMARY_TOOLBAR)

        self.btn_cast = Gtk.RadioToolButton(group=None)
        self.btn_cast.set_label(_("Screencast"))
        self.btn_cast.set_tooltip_text(_("Record a video of your desktop."))
        img1 = Gtk.Image.new_from_file(os.path.join(prefs.datadir, "icons", "light", "screencast.png"))
        self.btn_cast.set_icon_widget(img1)
        self.btn_cast.set_active(True)
        self.btn_cast.set_name("MAIN_SCREENCAST")
        self.btn_cast.connect("toggled", self.cb_main_toggled)

        self.btn_shot = Gtk.RadioToolButton(group=self.btn_cast)
        self.btn_shot.set_label(_("Screenshot"))
        self.btn_shot.set_tooltip_text(_("Record a picture of your desktop."))
        img2 = Gtk.Image.new_from_file(os.path.join(prefs.datadir, "icons", "light", "screenshot-1.png"))
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
        self.btn_full.set_label(_("Fullscreen"))
        self.btn_full.set_tooltip_text(_("Capture contents of the current screen."))
        img3 = Gtk.Image.new_from_file(os.path.join(prefs.datadir, "icons", "dark", "fullscreen.png"))
        self.btn_full.set_icon_widget(img3)
        self.btn_full.set_active(True)
        self.btn_full.set_name("MODE_FULL")
        self.btn_full.connect("toggled", self.cb_record_mode_toggled)

        self.btn_allscreens = Gtk.RadioToolButton(group=self.btn_full)
        self.btn_allscreens.set_label(_("All Screens"))
        self.btn_allscreens.set_tooltip_text(_("Capture contents of all of your screens."))
        img4 = Gtk.Image.new_from_file(os.path.join(prefs.datadir, "icons", "dark", "all-screens.png"))
        self.btn_allscreens.set_icon_widget(img4)
        self.btn_allscreens.set_name("MODE_ALL")
        self.btn_allscreens.connect("toggled", self.cb_record_mode_toggled)

        self.btn_area = Gtk.RadioToolButton(group=self.btn_full)
        self.btn_area.set_label(_("Area"))
        self.btn_area.set_tooltip_text(_("Capture a pre-selected area of your screen."))
        img5 = Gtk.Image.new_from_file(os.path.join(prefs.datadir, "icons", "dark", "area.png"))
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
        if prefs.sound:
            prefs.get_audio_sources()

        if not prefs.silent:
            self.window.show_all()
        else:
            logger.info("Starting in silent mode:\n  SUPER-CTRL-W to toggle main window.\n  SUPER-CTRL-Q to quit.")

        self.restore_UI()

        if not prefs.sound:
            self.combobox_audio.set_sensitive(False)
            self.combobox_audio2.set_sensitive(False)
            self.volumebutton_audio.set_sensitive(False)
            self.volumebutton_audio2.set_sensitive(False)


        HW.get_current_screen(self.window)
        self.startup = False

    #
    # Callbacks, go down here ...
    #

    #
    # Mode of operation toggles
    #
    def cb_main_toggled(self, widget):
        name = widget.get_name()
        if name == "MAIN_SCREENCAST" and widget.get_active():
            logger.debug("Main toggled: {0}".format(name))
            self.main_mode = MODE_SCREENCAST
            self.ntb_main.set_current_page(0)

        elif name == "MAIN_SCREENSHOT" and widget.get_active():
            logger.debug("Main toggled: {0}".format(name))
            self.main_mode = MODE_SCREENSHOT
            self.ntb_main.set_current_page(1)

    #
    # Record mode toggles
    #
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
            logger.debug("Capture full screen.")
            self.record_mode = MODE_FULL

        if widget.get_name() == "MODE_ALL" and widget.get_active():
            logger.debug("Capture all screens.")
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
        HW.get_screens()

    def cb_configure_event(self, widget, event):
        if event.type == Gdk.EventType.CONFIGURE:
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

        self.save_config()

        if prefs.sound:
            prefs.pa_q.end()

        Gtk.main_quit()

    def cb_preferences_request(self, indicator):
        logger.debug("Preferences requested.")
        self.preferences_window = Preferences()
        self.preferences_window.open()

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

    def cb_start_request(self, widget):
        logger.debug("Start recording selected.")
        self.run_counter()

    def cb_record_clicked(self, widget):
        logger.debug("Record clicked, invoking Screencast.")
        self.run_counter()

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
                                            prefs.codec,
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

    def cb_check_cursor(self, widget):
        prefs.capture_cursor = widget.get_active()
        logger.debug("Capture cursor: {0}.".format(prefs.capture_cursor))

    def cb_check_speakers(self, widget):
        prefs.capture_speakers = widget.get_active()
        logger.debug("Capture speakers: {0}.".format(prefs.capture_speakers))

    def cb_check_microphone(self, widget):
        prefs.capture_microphone = widget.get_active()
        logger.debug("Capture microphone: {0}.".format(prefs.capture_microphone))

    def cb_spinbutton_delay_change(self, widget):
        prefs.countdown_timer = widget.get_value_as_int()
        logger.debug("Start delay now: {0}".format(prefs.countdown_timer))

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

        self.recorder = Screencast()
        self.indicator.blink_set_state(BLINK_START)

        if prefs.sound:
            if prefs.capture_speakers and prefs.audio_source > 0:
                audio_source = prefs.audio_sources[prefs.audio_source][1]
            else:
                audio_source = None

            if prefs.capture_microphone and self.audio2_source > 0:
                audio2_source = prefs.audio_sources[self.audio2_source][1]
            else:
                audio2_source = None
        else:
            audio_source = None
            audio2_source = None

        #
        # Get appropriate coordinates for recording
        #

        if self.record_mode == MODE_FULL:
            screen = HW.get_current_screen(self.window)
            video_source = HW.screens[screen]
        elif self.record_mode == MODE_ALL:
            video_source = HW.combined_screen
        elif self.record_mode == MODE_AREA:
            video_source = None

        self.recorder.setup_sources(video_source,
                                    audio_source,
                                    audio2_source,
                                    self.area if self.record_mode == MODE_AREA else None)

        self.recorder.connect("flush-done", self.cb_flush_done)
        self.countdown = CountdownWindow(self.indicator, show_window = prefs.countdown_splash)
        self.countdown.connect("counter-finished", self.cb_counter_finished)
        self.countdown.run(prefs.countdown_timer)
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

        prefs.audio_source = self.config.getint("main", "audio_source")
        prefs.audio2_source = self.config.getint("main", "audio2_source")
        logger.debug("Restoring Audio source state: {0} {1}".format(
            prefs.audio_source,
            prefs.audio2_source))

        self.main_x = self.config.getint("main", "last_x")
        self.main_y = self.config.getint("main", "last_y")

        prefs.codec = self.config.getint("main", "codec")

        prefs.countdown_timer = self.config.getfloat("main", "counter")
        prefs.framerate = self.config.getfloat("main", "framerate")

        prefs.capture_cursor = self.config.getboolean("main", "capture_cursor")
        prefs.capture_microphone = self.config.getboolean("main", "capture_microphone")
        prefs.capture_speakers = self.config.getboolean("main", "capture_speakers")
        prefs.countdown_splash = self.config.getboolean("main", "countdown_splash")

    def restore_UI (self):

        self.window.move(self.main_x, self.main_y)
        self.chk_cursor.set_active(prefs.capture_cursor)
        self.chk_speakers.set_active(prefs.capture_speakers)
        self.chk_microphone.set_active(prefs.capture_microphone)
        self.spinbutton_delay.set_value(prefs.countdown_timer)

    def save_config(self):
        logger.debug("Saving state.")

        self.config.set("main", "capture_cursor", prefs.capture_cursor)
        self.config.set("main", "capture_speakers", prefs.capture_speakers)
        self.config.set("main", "capture_microphone", prefs.capture_microphone)

        self.config.set("main", "last_x", self.main_x)
        self.config.set("main", "last_y", self.main_y)

        if prefs.sound:
            logger.debug("Saving Audio source state: {0} {1}".format(
                                                                     prefs.audio_source,
                                                                     prefs.audio2_source))

            self.config.set("main", "audio_source", prefs.audio_source)
            self.config.set("main", "audio2_source", prefs.audio2_source)

        self.config.set("main", "countdown_splash", prefs.countdown_splash)
        self.config.set("main", "counter", prefs.countdown_timer)

        self.config.set("main", "codec", prefs.codec)

        self.config.set("main", "framerate", prefs.framerate)
        self.config.write()

