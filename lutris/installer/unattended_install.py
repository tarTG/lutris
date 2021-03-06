"""Install games without GUI"""
# Standard Library
import os
import time
from gettext import gettext as _

# Lutris Modules
from lutris import settings
from lutris.installer import interpreter
from lutris.installer.errors import MissingGameDependency, ScriptingError
from lutris.util import jobs, system
from lutris.util.downloader import Downloader
from lutris.util.log import logger


class UnattendedInstall:

    """unattended install process."""

    def __init__(
            self,
            game_slug=None,
            installer_file=None,
            revision=None,
            binary_path=None,
            install_path=None,
            install_options=None,
            cmd_print=None,  # get commandline output from application.py
            commandline=None,
            quit_callback=None
    ):  # pylint: disable=too-many-arguments

        self.interpreter = None
        self.game_slug = game_slug
        self.installer_file = installer_file
        self.revision = revision
        self.binary_path = binary_path
        self.install_path = install_path
        self.install_options = install_options
        self.downloader = None
        self.bin_path_coutner = 0  # iterating throught the files
        self.options_coutner = 0   # iterating throught the options
        self._print = cmd_print
        self.commandline = commandline
        self.quit_callback = quit_callback

        if system.path_exists(self.installer_file):
            self.on_scripts_obtained(interpreter.read_script(self.installer_file))
        else:
            self._print(self.commandline, _("Waiting for response from %s" % settings.SITE_URL))
            logger.debug(_("Waiting for response from %s" % settings.SITE_URL))
            jobs.AsyncCall(
                interpreter.fetch_script,
                self.on_scripts_obtained,
                self.game_slug,
                self.revision
            )

    def on_scripts_obtained(self, scripts, _error=None):
        if not scripts:
            self.on_install_error("No install script found")

        if isinstance(scripts, list):  # if scripts is a list longer than one element
            if len(scripts) != 1:
                self.on_install_error("Please provide single installer")

            self.script = scripts[0]
        else:
            self.script = scripts

        if self.validate_scripts() != 0:
            return
        self.prepare_install(self.script)

    def validate_scripts(self):
        """Auto-fixes some script aspects and checks for mandatory fields"""
        # check correct syntax
        for item in ["description", "notes"]:
            self.script[item] = self.script.get(item) or ""
        for item in ["name", "runner", "version"]:
            if item not in self.script:
                self.on_install_error(_("Invalid script: %s" % self.script))
                return -1

        # Check if given files match the number of needed files in the skript. Also test if the files available
        if "files" in self.script["script"]:
            req_files = list(filter(lambda file: "N/A" in file[next(iter(file))], self.script["script"]["files"]))
            if len(req_files) != 0:  # do we need an argument
                if len(req_files) != len(self.binary_path):  # is the number of arguments correct
                    self.on_install_error(_("Number of provided files is wrong. %s instead of %s")
                                          % (len(self.binary_path), len(req_files)))
                    return -1
                # check if files available
                for f in self.binary_path:
                    if not system.path_exists(f):
                        self.on_install_error(_("File %s does not exist") % f)
                        return -1

        # Check if given options match the number of needed options in the skript
        # Also test if the options are valid. The Options must be provided in correct order required from the skript
        menus = list(filter(lambda file: "input_menu" in file, self.script["script"]["installer"]))
        if len(menus) != 0:   # do we need an option
            if len(menus) != len(self.install_options):  # is the number of arguments correct
                self.on_install_error(_("Number of provided options is wrong. %s instead of %s")
                                      % (len(self.install_options), len(menus)))
                return -1

            # check content
            for count, menu in enumerate(menus):
                if len(list(filter(lambda file, i=count: self.install_options[i] in file,
                                   menu["input_menu"]["options"]))) == 0:
                    self.on_install_error(_("Option %s not available for menu %s")
                                          % (self.install_options[count], count))
                    return -1
        return 0

    def prepare_install(self, script):

        install_script = script
        if not install_script:
            self.on_install_error(_("Could not find script %s" % install_script))
            return
        try:
            self.interpreter = interpreter.ScriptInterpreter(install_script, self)
        except MissingGameDependency as ex:
            # call recursive dependencies
            UnattendedInstall(
                game_slug=ex.slug
            )

        self.select_install_folder()

    def select_install_folder(self):
        """Stage where we select the install directory."""
        if self.interpreter.creates_game_folder:
            if self.install_path is None:
                self.install_path = self.interpreter.get_default_target()

            self.interpreter.target_path = self.install_path
            self._print(self.commandline, _("install Folder %s" % self.interpreter.target_path))
            logger.debug(_("install Folder %s" % self.interpreter.target_path))

        try:
            self.interpreter.check_runner_install()
        except ScriptingError as ex:
            self.on_install_error(ex.__str__)
            return

    def set_status(self, text):
        self._print(self.commandline, text)

    # required by interpreter
    def clean_widgets(self):
        pass

    # required by interpreter
    def add_spinner(self):
        pass

    # required by interpreter
    def set_cancel_butten_sensitive(self, sensitivity):
        pass

    # required by interpreter
    def continue_button_hide(self):
        pass

    # required by interpreter
    def attach_logger(self, command):
        pass

    def on_install_error(self, message):
        self._print(self.commandline, message)
        logger.error(message)
        self.quit_callback()
        # end program

    def on_install_finished(self):
        self._print(self.commandline, "finished install")
        logger.debug("finished install")
        self.quit_callback()
        # end program

    def input_menu(self, alias, options, preselect, has_entry, callback):
        """Display an input request as a dropdown menu with options."""

        # if values are valid is checked in validate_scripts() function
        self.interpreter.user_inputs.append({"alias": alias, "value": self.install_options[self.options_coutner]})
        self.options_coutner += 1
        self.interpreter._iter_commands()

    def ask_user_for_file(self, message):
        if not os.path.isfile(self.binary_path[self.bin_path_coutner]):
            self.on_install_error(_("%s is not a file" % self.binary_path[self.bin_path_coutner]))
            return
        logger.info("use %s", self.binary_path[self.bin_path_coutner])
        self.interpreter.file_selected(self.binary_path[self.bin_path_coutner])
        self.bin_path_coutner += 1

    def start_download(self, file_uri, dest_file, callback=None, data=None, referer=None):
        try:
            self.downloader = Downloader(file_uri, dest_file, referer=referer, overwrite=True)
        except RuntimeError as ex:
            self.on_install_error(_("Downloading  %s to %s has an error: %s") %
                                  (file_uri, dest_file, ex.__str__))
            return None

        self._print(self.commandline, _("Downloading %s to %s") % (file_uri, dest_file))
        logger.debug("Downloading %s to %s", file_uri, dest_file)
        self.downloader.start()

        while self.downloader.check_progress() != 1.0:
            self.download_progress()
            time.sleep(0.5)

        self.on_download_complete(callback, data)

    def download_progress(self):
        """Show download progress."""
        if self.downloader.state in [self.downloader.CANCELLED, self.downloader.ERROR]:
            if self.downloader.state == self.downloader.CANCELLED:
                self.on_install_error("Download interrupted")
            else:
                self.on_install_error(self.downloader.error)
            if self.downloader.state == self.downloader.CANCELLED:
                self.on_install_error("Download canceled")
            return
        megabytes = 1024 * 1024
        self._print(self.commandline, _((
            "{downloaded:0.2f} / {size:0.2f}MB ({speed:0.2f}MB/s), {time} remaining"
        ).format(
            downloaded=float(self.downloader.downloaded_size) / megabytes,
            size=float(self.downloader.full_size) / megabytes,
            speed=float(self.downloader.average_speed) / megabytes,
            time=self.downloader.time_left,
        )))

    def on_download_complete(self, callback=None, callback_data=None):
        """Action called on a completed download."""
        if callback:
            try:
                callback_data = callback_data or {}
                callback(**callback_data)
            except Exception as ex:  # pylint: disable:broad-except
                self.on_install_error(str(ex))
                return

        self.interpreter.abort_current_task = None
        self.interpreter.iter_game_files()

    def ask_for_disc(self, message, callback, requires):
        """Ask the user to do insert a CD-ROM."""
        callback(requires)
