from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast, no_type_check

import gi
import jsonschema
import watchdog.events as we
import watchdog.observers as wo

gi.require_version(namespace="Gtk", version="3.0")
gi.require_version(namespace="AppIndicator3", version="0.1")

from gi.repository import AppIndicator3, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # type: ignore


class Application(Gtk.Application):
    """
    Core class
    """

    def __init__(self, application_id: str) -> None:
        super().__init__(application_id=application_id)

        # Paired Folders
        self.paired_folders_app: Dict[str, PairedFolder] = {}
        self._appindicator: Optional[AppIndicator] = None
        self._gui: Optional[GUI] = None
        self._block_gui: bool = False
        self.setup_application()

    @property
    def appindicator(self) -> AppIndicator:
        return cast(AppIndicator, self._appindicator)

    @property
    def gui(self) -> GUI:
        return cast(GUI, self._gui)

    def setup_application(self) -> None:
        GLib.set_prgname(self.get_application_id())
        self.connect("startup", lambda gtk_application: self.read_config())
        self.connect("activate", lambda gtk_application: self.start_app())

    def read_config(self) -> None:
        """
        Reads from config file and sets the configuration based on it. Callback of application's "startup" signal
        """
        if not config_file.is_file():
            return

        # Reading from config.json file
        config_data: Dict[str, Dict[str, Dict[str, Union[str, Dict[str, Union[bool, int]]]]]] = {}
        with config_file.open(mode="r") as config_:
            try:
                config_data = json.load(fp=config_)
            except:
                pass

        if not config_data:
            return

        # Validating json file against schema
        is_schema_valid: bool = True
        try:
            jsonschema.validate(instance=config_data, schema=json_schema)
        except:
            is_schema_valid = False
            dialog = self.create_dialog(
                parent=None,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="An error occured reading the configuration file",
                text2="The data of the configuration does not follow the valid schema, no settings were read.",
            )
            self._block_gui = True
            dialog.run()
            dialog.destroy()
            self._block_gui = False

        # Validation of the data
        def validate_data() -> None:
            """
            Validates the data of the config file
            """
            valid_paired_folders: Dict[str, PairedFolder] = {}
            errors: Dict[str, List[str]] = {}

            for paired_folder in config_data["paired_folders"]:
                pf: PairedFolder = PairedFolder(
                    alias=paired_folder,
                    source=cast(str, config_data["paired_folders"][paired_folder]["source"]),
                    target=cast(str, config_data["paired_folders"][paired_folder]["target"]),
                    buffer_size=cast(Dict[str, int], config_data["paired_folders"][paired_folder]["options"])[
                        "buffer_size"
                    ],
                    include_hidden_files=cast(Dict[str, bool], config_data["paired_folders"][paired_folder]["options"])[
                        "include_hidden_files"
                    ],
                    autostart_sync=cast(Dict[str, bool], config_data["paired_folders"][paired_folder]["options"])[
                        "autostart_sync"
                    ],
                    is_config_saved=True,
                )
                errors_pf: List[str] = pf.validate_from_config(valid_paired_folders=valid_paired_folders)
                if len(errors_pf):
                    errors[pf.alias] = errors_pf
                else:
                    valid_paired_folders[str(uuid.uuid4())] = pf

            # Show the errors on a dialog if there is any
            if len(errors):
                text: str = "There are some errors on the data in the configuration file, the invalid settings were not applied."
                for key, value in errors.items():
                    text += f"\n\n<b>{key}:</b>"
                    for err in value:
                        text += f"\n    - {err}"
                dialog = self.create_dialog(
                    parent=None,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="An error occured reading the configuration file",
                    text2=text,
                )

                def cb_dialog() -> None:
                    self._block_gui = False
                    dialog.destroy()

                self._block_gui = True
                dialog.connect("response", lambda gtk_dialog, responde_id: cb_dialog())
                dialog.show()

            self.paired_folders_app = valid_paired_folders

        # If the config file follows the valid jsonschema then we validate the data
        if is_schema_valid:
            validate_data()

    def start_app(self) -> None:
        """
        Initializes the AppIndicator and the User Interface. Callbak of applications "activate" signal
        """
        if self._appindicator is None:
            self._appindicator = AppIndicator(application=self)
        if self._gui is None:
            self._gui = GUI(application=self, builder=self.get_new_builder())
        else:  # Show main window when there's a try to open a new instance of the application
            self.gui.window.present()

    def create_dialog(
        self,
        parent: Optional[Gtk.Window],
        message_type: Gtk.MessageType,
        buttons: Gtk.ButtonsType,
        text: str,
        text2: str,
        modal: bool = True,
    ) -> Gtk.MessageDialog:
        """
        Creates a Gtk.MessageDialog and returns it
        """
        dialog: Gtk.MessageDialog = Gtk.MessageDialog(
            title=global_title, parent=parent, modal=modal, message_type=message_type, buttons=buttons, text=text
        )
        if len(text2):
            dialog.format_secondary_markup(message_format=text2)
        dialog.set_icon(icon=GdkPixbuf.Pixbuf.new_from_file(filename=str(icon)))
        if parent is not None:
            dialog.set_skip_taskbar_hint(setting=parent.is_visible())
        dialog.set_position(position=Gtk.WindowPosition.CENTER_ALWAYS)
        return dialog

    def create_file_chooser_dialog(
        self, parent: Optional[Gtk.Window], title: str, modal: bool = True, path: Optional[Path] = None
    ) -> Gtk.FileChooserDialog:
        """
        Creates a Gtk.FileChooserDialog with specified title and current folder
        """
        file_chooser_dialog: Gtk.FileChooserDialog = Gtk.FileChooserDialog(
            action=Gtk.FileChooserAction.SELECT_FOLDER, parent=parent, title=title
        )
        file_chooser_dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        file_chooser_dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        file_chooser_dialog.set_filename(filename=str(Path.home()) if path is None else str(path))
        file_chooser_dialog.set_modal(modal=modal)

        filter_dialog: Gtk.FileFilter = Gtk.FileFilter()
        filter_dialog.add_mime_type(mime_type="inode/directory")
        file_chooser_dialog.set_filter(filter=filter_dialog)

        return file_chooser_dialog

    def get_new_builder(self) -> Gtk.Builder:
        """
        Returns a new instance of Gtk.Builder with the content of the ui file
        """
        return cast(Gtk.Builder, Gtk.Builder.new_from_string(string=ui_content, length=len(ui_content)))  # type: ignore

    @staticmethod
    def open_file_manager(path: Path) -> bool:
        """
        Open file manager using `org.freedesktop.FileManager1` interface
        """
        uri_path: str = f"file://{str(path)}"
        try:
            proxy_interface_file_manager: Gio.DBusProxy = Gio.DBusProxy.new_for_bus_sync(
                bus_type=Gio.BusType.SESSION,
                flags=Gio.DBusProxyFlags.NONE,
                info=None,
                name="org.freedesktop.FileManager1",
                object_path="/org/freedesktop/FileManager1",
                interface_name="org.freedesktop.FileManager1",
                cancellable=None,
            )
            parameters: GLib.Variant = GLib.Variant.new_tuple(
                GLib.Variant.new_array(
                    child_type=GLib.VariantType.new(type_string="s"),
                    children=[GLib.Variant.new_string(string=uri_path)],
                ),
                GLib.Variant.new_string(string=""),  # type: ignore
            )
            proxy_interface_file_manager.call_sync(
                method_name="ShowItems" if not str(path) == "/" else "ShowFolders",
                parameters=parameters,
                flags=Gio.DBusCallFlags.NONE,
                timeout_msec=-1,
                cancellable=None,
            )
            return True
        except:
            return False

    def app_save_config(self, uuid: Optional[str] = None, exclude: bool = False) -> bool:
        """
        Save configuration to configuration file
        """

        config_to_save: Dict[str, Dict[str, Dict[str, Union[str, Dict[str, Union[int, bool]]]]]] = {
            "paired_folders": {}
        }
        if uuid is not None:  # Save single paired folder
            for uuid_, paired_folder in self.paired_folders_app.items():
                if (uuid != uuid_) and not paired_folder._is_config_saved:
                    continue
                if exclude and (uuid == uuid_):
                    continue
                config_to_save["paired_folders"][
                    paired_folder.alias if uuid == uuid_ else cast(str, paired_folder._original_state["alias"])
                ] = paired_folder.build_json(original_state=uuid != uuid_)
        else:  # Save all configured tabs to configuration file
            for uuid_, paired_folder in self.paired_folders_app.items():
                if not paired_folder.is_valid:
                    continue
                config_to_save["paired_folders"][paired_folder.alias] = paired_folder.build_json()

        result: bool = True
        try:
            with config_file.open(mode="w") as file_:
                json.dump(obj=config_to_save, fp=file_, ensure_ascii=False, indent=4)
        except:
            result = False
        return result

    def exit_(self) -> None:
        try:
            self.gui._notebook.stop_observers()
            self.quit()
        except Exception as e:
            print(f"EXCEPTION: {e}")
            exit()

    ## Signals
    @GObject.Signal(  # type: ignore
        name="app-add-paired-folder",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_STRING,
        arg_types=[GObject.TYPE_STRING],
    )
    def app_add_paired_folder(self, alias: str) -> str:
        uuid_: str = str(uuid.uuid4())
        self.paired_folders_app[uuid_] = PairedFolder(alias=alias)
        return uuid_

    @GObject.Signal(  # type: ignore
        name="app-update-paired-folder",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING, GObject.TYPE_STRING, GObject.TYPE_PYOBJECT],
    )
    def app_update_paired_folder(self, uuid: str, key: str, value: Optional[Union[bool, int, Path]]) -> None:
        setattr(self.paired_folders_app[uuid], key, value)

    @GObject.Signal(  # type: ignore
        name="app-delete-paired-folder",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING],
    )
    def app_delete_paired_folder(self, uuid: str) -> None:
        if not self.paired_folders_app[uuid]._is_config_saved:
            del self.paired_folders_app[uuid]

    @GObject.Signal(  # type: ignore
        name="app-start-stop-sync",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING, GObject.TYPE_BOOLEAN],
    )
    def app_start_stop_sync(self, tab_uuid: str, start: bool) -> None:
        if self._block_gui:
            return

        self.gui._notebook.start_stop_tab_sync(tab_uuid=tab_uuid, start=start)

    @GObject.Signal(  # type: ignore
        name="gui-show-main-window", flags=GObject.SignalFlags.RUN_LAST, return_type=GObject.TYPE_NONE
    )
    def gui_show_main_window(self) -> None:
        self.gui.window.present()

    @GObject.Signal(  # type: ignore
        name="gui-show-tab",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING],
    )
    def gui_show_tab(self, tab_uuid: str) -> None:
        if self._block_gui:
            return

        self.gui.window.present()
        self.gui._notebook.show_tab(tab_uuid=tab_uuid)

    @GObject.Signal(  # type: ignore
        name="gui-show-info-textview",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING, GObject.TYPE_PYOBJECT],
    )
    def gui_show_info_text_view(self, tab_uuid: str, info_to_show: Dict[str, str]) -> None:
        GLib.idle_add(lambda: self.gui._notebook.show_info_tab_textview(tab_uuid=tab_uuid, info=info_to_show))

    @GObject.Signal(  # type: ignore
        name="appindicator-add-paired-folder",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_PYOBJECT, GObject.TYPE_STRING],
    )
    def appindicator_add_paired_folder(self, paired_folder: PairedFolder, uuid_: str) -> None:
        self.appindicator.add_new_paired_folder(paired_folder=paired_folder, uuid_=uuid_)

    @GObject.Signal(  # type: ignore
        name="appindicator-delete-paired-folder",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING],
    )
    def appindicator_delete_paired_folder(self, tab_uuid: str) -> None:
        self.appindicator.delete_paired_folder(tab_uuid=tab_uuid)

    @GObject.Signal(  # type: ignore
        name="appindicator-update-alias-or-create-item",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING, GObject.TYPE_PYOBJECT],
    )
    def appindicator_update_alias_or_create_item(self, tab_uuid: str, paired_folder: PairedFolder) -> None:
        self.appindicator.update_alias_or_create_item(tab_uuid=tab_uuid, paired_folder=paired_folder)

    @GObject.Signal(  # type: ignore
        name="appindicator-update-item-sync",
        flags=GObject.SignalFlags.RUN_LAST,
        return_type=GObject.TYPE_NONE,
        arg_types=[GObject.TYPE_STRING],
    )
    def appindicator_update_item_sync(self, tab_uuid: str) -> None:
        self.appindicator.update_item_based_on_sync(tab_uuid=tab_uuid)


class GUI:
    """
    Main graphical interface class
    """

    class Tab:
        """
        Class representing every tab of the interface
        """

        def __init__(
            self,
            parent: GUI.Notebook,
            builder: Gtk.Builder,
            uuid_paired_folder: Optional[str],
            include_close_button: bool = True,
        ) -> None:
            self._notebook: GUI.Notebook = parent
            self._builder: Gtk.Builder = builder
            self._uuid_paired_folder: Optional[str] = uuid_paired_folder

            self.container: Gtk.Box = cast(Gtk.Box, self._builder.get_object(name="containerSingleTab"))
            self.title_container: Gtk.Box = cast(Gtk.Box, self._builder.get_object(name="containerTitleSingleTab"))
            self.label_tab: Gtk.Label = cast(Gtk.Label, self._builder.get_object(name="labelSingleTab"))
            self.button_start_stop_sync: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonControlSync")
            )
            self.button_save_config: Gtk.Button = cast(Gtk.Button, self._builder.get_object(name="buttonSaveConfig"))
            self.button_delete_config: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonDeleteConfig")
            )

            # TextView
            self.buffer_text_view: Gtk.TextBuffer = cast(
                Gtk.TextView, self._builder.get_object(name="textView")
            ).get_buffer()
            self.tags_buffer: Dict[str, Gtk.TextTag] = {}

            # Status Bar
            self.status_bar: Gtk.Statusbar = cast(Gtk.Statusbar, self._builder.get_object(name="statusBar"))

            # Observer
            self.folder_observer: Optional[FolderObserver] = None

            self.configure_font_and_tags()
            self.configure_buffer()
            self.setup_tab(include_close_button=include_close_button)

        @property
        def paired_folder(self) -> PairedFolder:
            uuid_: str = cast(str, self._uuid_paired_folder)
            return self._notebook._gui._application.paired_folders_app[uuid_]

        def setup_tab(self, include_close_button: bool) -> None:
            self.button_start_stop_sync.connect("clicked", lambda gtk_button: self.start_stop_sync())

            self.button_save_config.connect("clicked", lambda gtk_button: self.save_config())
            self.button_save_config.set_tooltip_markup(markup="Save current configuration to configuration file")
            self.button_save_config.set_has_tooltip(has_tooltip=False)

            self.button_delete_config.connect("clicked", lambda gtk_button: self.delete_config())
            self.button_delete_config.set_tooltip_markup(markup="Delete current configuration from configuration file")
            self.button_delete_config.set_has_tooltip(has_tooltip=True)

            new_alias: str = (
                f"NewPairedFolder{len(self._notebook.tab_list)+1}"
                if self._uuid_paired_folder is None
                else self.paired_folder.alias
            )
            self.label_tab.set_markup(str=new_alias)

            if self._uuid_paired_folder is None:
                self._uuid_paired_folder = self._notebook._gui._application.emit("app-add-paired-folder", new_alias)  # type: ignore

            button_close_tab: Gtk.Button = cast(Gtk.Button, self._builder.get_object(name="buttonCloseSingleTab"))
            button_close_tab.set_has_tooltip(has_tooltip=True)
            button_close_tab.set_tooltip_markup(markup="Close this tab")
            signal_close_tab: int = button_close_tab.connect(
                "clicked", lambda gtk_button: self._notebook.delete_tab(tab=self)
            )

            if not include_close_button:
                button_close_tab.set_image(image=Gtk.Image.new())
                button_close_tab.disconnect(signal_close_tab)
                button_close_tab.connect(
                    "clicked", lambda gtk_button: self._notebook.notebook.set_current_page(page_num=0)
                )

            label_source_folder: Gtk.Label = cast(Gtk.Label, self._builder.get_object(name="labelSourceFolder"))
            label_source_folder.connect(
                "activate-link", lambda gtk_label, uri: Application.open_file_manager(path=self.paired_folder.source)
            )

            label_target_folder: Gtk.Label = cast(Gtk.Label, self._builder.get_object(name="labelTargetFolder"))
            label_target_folder.connect(
                "activate-link", lambda gtk_label, uri: Application.open_file_manager(path=self.paired_folder.target)
            )

            button_select_source_folder: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonOpenDialogSelectSourceFolder")
            )
            button_select_source_folder.set_has_tooltip(has_tooltip=True)
            button_select_source_folder.set_tooltip_markup(markup="Select source folder")
            button_select_source_folder.connect(
                "clicked",
                lambda gtk_button: self.open_file_chooser_dialog(
                    path=self.paired_folder.source,
                    label=label_source_folder,
                    opposite_label=label_target_folder,
                    property_to_update="_source",
                ),
            )

            button_select_target_folder: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonOpenDialogSelectTargetFolder")
            )
            button_select_target_folder.set_has_tooltip(has_tooltip=True)
            button_select_target_folder.set_tooltip_markup(markup="Select target folder")
            button_select_target_folder.connect(
                "clicked",
                lambda gtk_button: self.open_file_chooser_dialog(
                    path=self.paired_folder.target,
                    label=label_target_folder,
                    opposite_label=label_source_folder,
                    property_to_update="_target",
                ),
            )

            checkbutton_include_hidden_files: Gtk.CheckButton = cast(
                Gtk.CheckButton, self._builder.get_object(name="checkButtonIncludeHiddenFiles")
            )
            checkbutton_autostart_sync: Gtk.CheckButton = cast(
                Gtk.CheckButton, self._builder.get_object(name="checkButtonAutostartSync")
            )

            markup: str = "Set the desired buffer size.\n"
            markup += f"<b>Default value:</b> {default_buffer_size}\n"
            markup += f"<b>Min value:</b> {min_buffer_size}\n"
            markup += f"<b>Max value:</b> {max_buffer_size}"
            entry_history_size: Gtk.Entry = cast(Gtk.Entry, self._builder.get_object(name="entryHistorySize"))
            entry_history_size.set_has_tooltip(has_tooltip=True)
            entry_history_size.set_tooltip_markup(markup=markup)
            entry_history_size.connect(
                "icon-press",
                lambda gtk_entry, gtk_entry_icon_position, gdk_event: entry_history_size.set_text(
                    text=str(default_buffer_size)
                ),
            )

            # Status bar default text
            self.status_bar.push(context_id=1, text="Synchronization inactive")

            # This will set the configuration read from the config file
            if self.paired_folder._is_config_saved:
                self.show_text_textview(mode="read-config")
                path_name: str = f"{self.paired_folder.source.name if len(self.paired_folder.source.name) else str(self.paired_folder.source)}"
                label_source_folder.set_markup(str=f'<a href="file://{str(self.paired_folder.source)}">{path_name}</a>')

                path_name = f"{self.paired_folder.target.name if len(self.paired_folder.target.name) else str(self.paired_folder.target)}"
                label_target_folder.set_markup(str=f'<a href="file://{str(self.paired_folder.target)}">{path_name}</a>')

                checkbutton_include_hidden_files.set_active(is_active=self.paired_folder.include_hidden_files)
                checkbutton_autostart_sync.set_active(is_active=self.paired_folder.autostart_sync)

                entry_history_size.set_text(text=str(self.paired_folder.buffer_size))
                self.check_status_path()
                self.button_delete_config.set_visible(visible=True)
                if self.paired_folder.autostart_sync:
                    self.start_stop_sync(start=True, startup=True)

            def cb_options(property_to_update: str, value: Union[int, bool]) -> None:
                """
                Callback for the checkbuttons and the entry
                """
                self._notebook._gui._application.emit(
                    "app-update-paired-folder", self._uuid_paired_folder, property_to_update, value
                )

            def cb_entry_history_size_focus_out() -> bool:
                """
                Callback of history size entry when it loses focus
                """
                history_size = entry_history_size.get_text()
                if not len(history_size) or (
                    int(history_size) > max_buffer_size or int(history_size) < min_buffer_size
                ):
                    entry_history_size.set_text(text=str(default_buffer_size))

                if int(history_size) != self.paired_folder.buffer_size:
                    cb_options(property_to_update="buffer_size", value=int(entry_history_size.get_text()))
                return False

            checkbutton_include_hidden_files.connect(
                "toggled",
                lambda gtk_toggle_button: cb_options(
                    property_to_update="include_hidden_files", value=checkbutton_include_hidden_files.get_active()
                ),
            )
            checkbutton_autostart_sync.connect(
                "toggled",
                lambda gtk_toggle_button: cb_options(
                    property_to_update="autostart_sync", value=checkbutton_autostart_sync.get_active()
                ),
            )
            entry_history_size.connect(
                "focus-out-event", lambda gtk_entry, gdk_event: cb_entry_history_size_focus_out()
            )

        def configure_font_and_tags(self) -> None:
            monospaced_font: str = "Monospace"
            font_size: int = 11

            try:
                settings: Optional[Gio.SettingsSchema] = Gio.SettingsSchemaSource.get_default().lookup(
                    schema_id="org.gnome.desktop.interface", recursive=True
                )
                if settings is not None:
                    interface_settings: Gio.Settings = Gio.Settings.new(schema_id="org.gnome.desktop.interface")

                    current_font: str = interface_settings.get_string(key="font-name")
                    if len(current_font):
                        font_size = int(current_font.split(sep=" ")[-1])
                        if font_size < 11:
                            font_size = 12
                    monospaced_font = interface_settings.get_string(key="monospace-font-name")
            except:
                pass

            # Custom monospaced font with env var MONOSPACED_FONT
            custom_monospaced_font_name: Optional[str] = os.environ.get("MONOSPACED_FONT")
            if custom_monospaced_font_name is not None:
                pango_context: Pango.Context = self._notebook._gui.window.get_pango_context()
                for family in pango_context.list_families():
                    family = cast(Pango.FontFamily, family)
                    if (
                        custom_monospaced_font_name == family.get_name()
                        or family.get_name() in custom_monospaced_font_name
                    ):
                        monospaced_font = family.get_name()
                        break

            # Tags
            tag_normal: Gtk.TextTag = self.buffer_text_view.create_tag(
                tag_name="normal", **{"weight": Pango.Weight.NORMAL, "size-points": float(font_size)}
            )

            tag_title: Gtk.TextTag = self.buffer_text_view.create_tag(
                tag_name="title", **{"weight": Pango.Weight.BOLD, "size-points": 15.0}
            )

            tag_bold: Gtk.TextTag = self.buffer_text_view.create_tag(tag_name="bold", **{"weight": Pango.Weight.BOLD})

            tag_monospaced: Gtk.TextTag = self.buffer_text_view.create_tag(
                tag_name="monospaced", **{"font": monospaced_font, "size-points": 12.5}
            )

            tag_monospaced_bold: Gtk.TextTag = self.buffer_text_view.create_tag(
                tag_name="monospaced_bold",
                **{"font": monospaced_font, "weight": Pango.Weight.BOLD, "size-points": 12.5},
            )

            tag_centered: Gtk.TextTag = self.buffer_text_view.create_tag(
                tag_name="centered", **{"justification": Gtk.Justification.CENTER}
            )

            self.tags_buffer.update(
                {
                    "normal": tag_normal,
                    "title": tag_title,
                    "bold": tag_bold,
                    "monospaced": tag_monospaced,
                    "monospaced_bold": tag_monospaced_bold,
                    "centered": tag_centered,
                }
            )

        def configure_buffer(self) -> None:
            @no_type_check
            def delete_lines_textview() -> None:
                lines_to_delete: int = self.buffer_text_view.get_line_count() - self.paired_folder.buffer_size
                start: Gtk.TextIter = self.buffer_text_view.get_start_iter()
                end: Gtk.TextIter = self.buffer_text_view.get_iter_at_line(line_number=lines_to_delete)
                self.buffer_text_view.handler_block(signal_id_textview_changed)
                self.buffer_text_view.delete(start=start, end=end)
                self.buffer_text_view.handler_unblock(signal_id_textview_changed)

            def cb_textview_changed() -> None:
                if self.buffer_text_view.get_line_count() > self.paired_folder.buffer_size:
                    delete_lines_textview()

            signal_id_textview_changed = self.buffer_text_view.connect(
                "changed", lambda gtk_text_buffer: cb_textview_changed()
            )

        def open_file_chooser_dialog(
            self, path: Path, label: Gtk.Label, opposite_label: Gtk.Label, property_to_update: str
        ) -> None:
            """
            Open a file chooser dialog to choose source/target folder.

            :param Path path: Current path of file chooser
            :param Gtk.Label label: Label to update when source/target is chosen
            :param Gtk.Label opposite_label: Opposite label to update when source/target is chosen
            :param str property_to_update: Property to update when source/target is chosen
            """
            title_: str = f"{global_title} - Select {property_to_update[1:]} folder"
            dialog = self._notebook._gui._application.create_file_chooser_dialog(
                parent=self._notebook._gui.window, title=title_, path=path
            )
            self._notebook._gui._application._block_gui = True
            response: int = dialog.run()
            file_: Gio.File = dialog.get_file()  # Folder chosen by the dialog
            dialog.destroy()
            self._notebook._gui._application._block_gui = False
            if not response == Gtk.ResponseType.OK:
                return

            opposite_property: str = "_target" if property_to_update == "_source" else "_source"
            new_path: Path = Path(file_.get_path())
            label.set_markup(
                str=f'<a href="file://{str(new_path)}">{new_path.name if len(new_path.name) else str(new_path)}</a>'
            )

            def show_error(text: str) -> None:
                """
                Show a Gtk.MessageDialog displaying the error message
                """
                dialog: Gtk.MessageDialog = self._notebook._gui._application.create_dialog(
                    parent=self._notebook._gui.window,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Invalid path",
                    text2=text,
                )
                self._notebook._gui._application._block_gui = True
                dialog.run()
                old_path: Optional[Path] = cast(Optional[Path], getattr(self.paired_folder, property_to_update))
                old_markup: str = (
                    f'<a href="file://{str(old_path)}">{old_path.name if len(old_path.name) else str(old_path)}</a>'
                    if old_path is not None
                    else "(None)"
                )
                label.set_markup(str=old_markup)
                dialog.destroy()
                self._notebook._gui._application._block_gui = False

            def check_permissions() -> None:
                """
                Check if the new path has the appropriate read/write permissions
                """
                permission_to_check: int = os.R_OK if property_to_update == "_source" else os.W_OK
                verb_to_show: str = "readable" if property_to_update == "_source" else "writable"
                if not os.access(path=new_path, mode=permission_to_check):
                    show_error(text=f"The chosen path is not {verb_to_show}, please choose another one.")
                    raise

            def check_path_validity_against_tabs() -> None:
                """
                Check if new path is in use by another tab
                """
                for tab in self._notebook.tab_list:
                    if self._notebook.notebook.get_current_page() == self._notebook.notebook.page_num(
                        child=tab.container
                    ):
                        continue
                    if tab.paired_folder._target is not None and (
                        new_path.resolve() == tab.paired_folder.target.resolve()
                    ):
                        show_error(
                            text=f'The chosen path is already in use by the configuration "<b>{tab.paired_folder.alias}</b>" on tab number {self._notebook.notebook.page_num(child=tab.container) + 1}.'
                        )
                        raise

            try:
                check_permissions()
                if property_to_update == "_target":  # Target folders have to be unique
                    check_path_validity_against_tabs()

                self._notebook._gui._application.emit(
                    "app-update-paired-folder", self._uuid_paired_folder, property_to_update, new_path
                )
                if new_path.resolve() == cast(Path, getattr(self.paired_folder, opposite_property)).resolve():
                    opposite_label.set_markup(str="(None)")
                    self._notebook._gui._application.emit(
                        "app-update-paired-folder", self._uuid_paired_folder, opposite_property, None
                    )

                self.check_status_path()
            except:
                pass

        def check_status_path(self) -> None:
            """
            Checks if source and target paths are not `None` and activates the buttons to start/stop sync and to save config, also, changes the alias when both source and target are valid
            """

            self.button_start_stop_sync.set_sensitive(sensitive=self.paired_folder.is_valid)
            self.button_save_config.set_has_tooltip(has_tooltip=self.paired_folder.is_valid)
            self.button_save_config.set_sensitive(sensitive=self.paired_folder.is_valid)

            self._notebook.option_save_current_tab.set_has_tooltip(has_tooltip=self.paired_folder.is_valid)
            self._notebook.option_save_current_tab.set_sensitive(sensitive=self.paired_folder.is_valid)

            if self.paired_folder.is_valid:
                source_name: str = (
                    self.paired_folder.source.name
                    if len(self.paired_folder.source.name)
                    else str(self.paired_folder.source)
                )
                target_name: str = (
                    self.paired_folder.target.name
                    if len(self.paired_folder.target.name)
                    else str(self.paired_folder.target)
                )
                new_alias: str = f"{source_name} --> {target_name}"
                self._notebook._gui._application.emit(
                    "app-update-paired-folder", self._uuid_paired_folder, "alias", new_alias
                )
                self.label_tab.set_markup(str=new_alias)

                self._notebook._gui._application.emit(
                    "appindicator-update-alias-or-create-item", self._uuid_paired_folder, self.paired_folder
                )

        def show_text_textview(self, mode: str) -> None:
            """
            Show text in textview, depending on `mode`, which can be:

            "read-config" to show text related with read from config file

            "save-config" to show text related with save config to config file

            "delete-config" to show text related with delete config from config file

            "start-stop-sync" to show text related with starting/stopping synchronization

            "start-sync-error" to show text related with errors when starting sync
            """

            def write_common_info(title: str) -> None:
                self.insert_text_with_tags(
                    text=f"{title}\n\n", tags=[self.tags_buffer["title"], self.tags_buffer["centered"]]
                )

                self.insert_text_with_tags(text="SOURCE: ", tags=[self.tags_buffer["monospaced_bold"]])
                self.insert_text_with_tags(
                    text=f"{str(self.paired_folder.source)}\n", tags=[self.tags_buffer["monospaced"]]
                )

                self.insert_text_with_tags(text="TARGET: ", tags=[self.tags_buffer["monospaced_bold"]])
                self.insert_text_with_tags(
                    text=f"{str(self.paired_folder.target)}\n", tags=[self.tags_buffer["monospaced"]]
                )

            def get_current_time_format() -> str:
                return f'[{datetime.datetime.now().strftime("%H:%M:%S")}]'

            if mode == "read-config":
                write_common_info(title="CONFIGURATION READ")

                self.insert_text_with_tags(text=f"{get_current_time_format()} The configuration section ")
                self.insert_text_with_tags(
                    text=f'"{self.paired_folder._original_state["alias"]}"', tags=[self.tags_buffer["bold"]]
                )
                self.insert_text_with_tags(text=" has been read from the configuration file.\n\n")
            elif mode == "save-config":
                write_common_info(title="CONFIGURATION SAVED")

                self.insert_text_with_tags(
                    text=f"{get_current_time_format()} The configuration has been saved in the section "
                )
                self.insert_text_with_tags(
                    text=f'"{self.paired_folder._original_state["alias"]}"', tags=[self.tags_buffer["bold"]]
                )
                self.insert_text_with_tags(text=" in the configuration file.\n\n")
            elif mode == "delete-config":
                self.insert_text_with_tags(
                    text="CONFIGURATION DELETED\n\n", tags=[self.tags_buffer["title"], self.tags_buffer["centered"]]
                )

                self.insert_text_with_tags(text=f"{get_current_time_format()} The configuration section ")
                self.insert_text_with_tags(
                    text=f'"{self.paired_folder._original_state["alias"]}"', tags=[self.tags_buffer["bold"]]
                )
                self.insert_text_with_tags(text=" has been deleted from the configuration file.\n\n")
            elif mode == "start-stop-sync":
                if self.paired_folder._synchronization_status:
                    write_common_info(title="SYNCHRONIZATION STARTED")

                    self.insert_text_with_tags(
                        text=f"{get_current_time_format()} The synchronization has started, all the events reported on source are going to be replicated on target.\n\n"
                    )
                else:
                    self.insert_text_with_tags(
                        text="SYNCHRONIZATION STOPPED\n\n",
                        tags=[self.tags_buffer["title"], self.tags_buffer["centered"]],
                    )

                    self.insert_text_with_tags(
                        text=f"{get_current_time_format()} The synchronization has been stopped.\n\n"
                    )
            elif mode == "start-sync-error":
                self.insert_text_with_tags(
                    text="SYNCHRONIZATION COULD NOT BE STARTED\n\n",
                    tags=[self.tags_buffer["title"], self.tags_buffer["centered"]],
                )

                self.insert_text_with_tags(
                    text=f"{get_current_time_format()} The synchronization could not be started, the source location is not valid.\n\n"
                )

        @no_type_check
        def insert_text_with_tags(self, text: str, tags: Optional[List[Gtk.TextTag]] = None) -> None:
            """
            Insert text in textview
            """
            if tags is None:
                tags = [self.tags_buffer["normal"]]
            self.buffer_text_view.insert_with_tags(self.buffer_text_view.get_end_iter(), text, *tags)

        def save_config(self) -> None:
            """
            Save configuration of current tab in config file
            """
            dialog: Gtk.MessageDialog = self._notebook._gui._application.create_dialog(
                parent=self._notebook._gui.window,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Save configuration",
                text2="Do you want to save the current configuration?",
            )
            if self.paired_folder._is_config_saved:
                if self.paired_folder.has_changed():
                    dialog.format_secondary_markup(
                        message_format=f'Do you want to save the current configuration? This will update the section <b>"{self.paired_folder._original_state["alias"]}</b>" in the configuration file.'
                    )
                else:
                    dialog = self._notebook._gui._application.create_dialog(
                        parent=self._notebook._gui.window,
                        message_type=Gtk.MessageType.INFO,
                        buttons=Gtk.ButtonsType.OK,
                        text="Save configuration",
                        text2=f'The configuration is already saved in the section <b>"{self.paired_folder.alias}"</b> in the configuration file.',
                    )
            self._notebook._gui._application._block_gui = True
            response_id: int = dialog.run()
            dialog.destroy()
            if response_id == Gtk.ResponseType.YES:
                result: bool = self._notebook._gui._application.app_save_config(uuid=self._uuid_paired_folder)
                text: str = (
                    f'The configuration has been saved in the section <b>"{self.paired_folder.alias}"</b> in the configuration file.'
                    if result
                    else "An error occurred saving the configuration to file."
                )
                dialog = self._notebook._gui._application.create_dialog(
                    parent=self._notebook._gui.window,
                    message_type=Gtk.MessageType.INFO if result else Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Save configuration",
                    text2=text,
                )
                if result:
                    self.paired_folder.update_config_after_save()
                    self.button_delete_config.set_visible(visible=True)
                    self.show_text_textview(mode="save-config")
                    self._notebook._gui.show_link_open_config_file()
                dialog.run()
                dialog.destroy()
            self._notebook._gui._application._block_gui = False

        def delete_config(self) -> None:
            """
            Delete tab's associated section from config file
            """
            dialog: Gtk.MessageDialog = self._notebook._gui._application.create_dialog(
                parent=self._notebook._gui.window,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Delete configuration",
                text2=f'Do you want to delete the configuration section <b>"{self.paired_folder._original_state["alias"]}"</b> from the configuration file?',
            )
            self._notebook._gui._application._block_gui = True
            response_id: int = dialog.run()
            dialog.destroy()
            if response_id == Gtk.ResponseType.YES:
                result: bool = self._notebook._gui._application.app_save_config(
                    uuid=self._uuid_paired_folder, exclude=True
                )
                text: str = (
                    f'The configuration section <b>"{self.paired_folder._original_state["alias"]}"</b> has been deleted from the configuration file.'
                    if result
                    else f'An error occurred deleting the section <b>"{self.paired_folder._original_state["alias"]}"</b> from the configuration to file.'
                )
                dialog = self._notebook._gui._application.create_dialog(
                    parent=self._notebook._gui.window,
                    message_type=Gtk.MessageType.INFO if result else Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Delete configuration",
                    text2=text,
                )
                if result:
                    self.paired_folder.update_config_after_save(config_saved=False)
                    self.show_text_textview(mode="delete-config")

                def cb_dialog() -> None:
                    if result:
                        self.button_delete_config.set_visible(visible=False)
                    dialog.destroy()

                dialog.connect("response", lambda gtrk_dialog, responde_id: cb_dialog())
                dialog.run()
            self._notebook._gui._application._block_gui = False

        def start_stop_sync(self, start: Optional[bool] = None, startup: bool = False) -> None:
            """
            Function that controls when starting/stopping synchronization
            """

            def control_sync(start: bool) -> None:
                """
                Control the flow of starting/stopping the synchronization and enables/disables the items
                """
                # Start/stop sync button
                imagen.set_from_icon_name(
                    icon_name="media-playback-stop-symbolic" if start else "media-playlist-shuffle-symbolic",
                    size=Gtk.IconSize.BUTTON,
                )
                label.set_text(str=f"{'STOP' if start else 'START'} SYNCHRONIZATION")

                # Source and target options
                button_select_source_folder.set_sensitive(sensitive=not start)
                button_select_source_folder.set_has_tooltip(has_tooltip=not start)

                button_select_target_folder.set_sensitive(sensitive=not start)
                button_select_target_folder.set_has_tooltip(has_tooltip=not start)
                if not start:
                    button_select_source_folder.set_tooltip_markup(markup="Select source folder")
                    button_select_target_folder.set_tooltip_markup(markup="Select target folder")

                # Container of include hidden files, buffer size and autostart sync options
                container_options.set_sensitive(sensitive=not start)
                entry_history_size.set_has_tooltip(has_tooltip=not start)
                if not start:
                    markup: str = "Set the desired buffer size.\n"
                    markup += f"<b>Default value:</b> {default_buffer_size}\n"
                    markup += f"<b>Min value:</b> {min_buffer_size}\n"
                    markup += f"<b>Max value:</b> {max_buffer_size}"
                    entry_history_size.set_tooltip_markup(markup=markup)

                # Status bar
                self.status_bar.pop(context_id=1)
                self.status_bar.push(context_id=1, text=f"Synchronization {'active' if start else 'inactive'}")

            def show_dialog(start: bool, error: bool = False) -> None:
                if self._notebook._gui.window.is_visible():
                    return
                text: str = (
                    f'The synchronization for the paired folder <b>"{self.paired_folder.alias}"</b> has {"started" if start else "been stopped"} successfully.'
                    if not error
                    else f'The synchronization for the paired folder <b>"{self.paired_folder.alias}"</b> could not be started, the source location is not valid.'
                )
                dialog: Gtk.MessageDialog = self._notebook._gui._application.create_dialog(
                    parent=self._notebook._gui.window if self._notebook._gui.window.is_visible() else None,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Synchronization status",
                    text2=text,
                )
                self._notebook._gui._application._block_gui = True
                dialog.run()
                dialog.destroy()
                self._notebook._gui._application._block_gui = False

            def start_stop_observer(start: bool) -> None:
                if self.folder_observer is not None and self.folder_observer.running:
                    self.folder_observer.stop()
                if start:
                    self.folder_observer = FolderObserver(
                        application=self._notebook._gui._application,
                        uuid_paired_folder=cast(str, self._uuid_paired_folder),
                    )
                else:
                    self.folder_observer = None

            if start is None:
                self.paired_folder._synchronization_status = not self.paired_folder._synchronization_status
            else:
                self.paired_folder._synchronization_status = start

            start_stop_observer(start=self.paired_folder._synchronization_status)
            if self.folder_observer is not None and not self.folder_observer.running:
                self.folder_observer = None
                self.paired_folder._synchronization_status = False
                self.show_text_textview(mode="start-sync-error")
                show_dialog(start=self.paired_folder._synchronization_status, error=True)
                return

            imagen: Gtk.Image = cast(Gtk.Image, self._builder.get_object(name="imagenBotonControlSincronizacion"))
            label: Gtk.Label = cast(Gtk.Label, self._builder.get_object(name="labelBotonControlSincronizacion"))

            button_select_source_folder: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonOpenDialogSelectSourceFolder")
            )
            button_select_target_folder: Gtk.Button = cast(
                Gtk.Button, self._builder.get_object(name="buttonOpenDialogSelectTargetFolder")
            )

            container_options: Gtk.Box = cast(Gtk.Box, self._builder.get_object(name="containerOptions"))
            entry_history_size: Gtk.Entry = cast(Gtk.Entry, self._builder.get_object(name="entryHistorySize"))

            control_sync(start=self.paired_folder._synchronization_status)
            self.show_text_textview(mode="start-stop-sync")
            if not startup:
                show_dialog(start=self.paired_folder._synchronization_status)
            self._notebook._gui._application.emit("appindicator-update-item-sync", self._uuid_paired_folder)

        def show_event_textview(self, info: Dict[str, str]) -> None:
            """
            Show information about an event and its replication
            """
            self.insert_text_with_tags(
                text=f'{datetime.datetime.now().strftime("%d/%m/%Y - %I:%M:%S %p")}\n',
                tags=[self.tags_buffer["monospaced_bold"]],
            )
            for key, value in info.items():
                self.insert_text_with_tags(text=f"{key}: ", tags=[self.tags_buffer["monospaced_bold"]])
                self.insert_text_with_tags(
                    text=f"{' ' if key == 'Event' else ''}{value}\n", tags=[self.tags_buffer["monospaced"]]
                )
            self.insert_text_with_tags(text="\n")

    class Notebook:
        """
        Class that handles the tab's header
        """

        def __init__(self, parent: GUI) -> None:
            self._gui: GUI = parent
            self.tab_list: List[GUI.Tab] = []
            self.notebook: Gtk.Notebook = cast(Gtk.Notebook, self._gui.builder.get_object(name="mainTabs"))
            self.option_save_current_tab: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Save configuration")
            self.setup_tabs_from_config()
            self.setup_notebook()

        def setup_notebook(self) -> None:
            self.option_save_current_tab.set_tooltip_markup(markup="Save current tab to configuration file")
            self.option_save_current_tab.connect(
                "activate", lambda gtk_menu_item: self.tab_list[self.notebook.get_current_page()].save_config()
            )

            button_add_new_tab: Gtk.Button = cast(Gtk.Button, self._gui.builder.get_object(name="buttonAddNewTab"))
            button_add_new_tab.set_has_tooltip(has_tooltip=True)
            button_add_new_tab.set_tooltip_markup(markup="Add a new tab to configure a pair of folders to synchronize")
            button_add_new_tab.connect("clicked", lambda gtk_button: self.add_tab())

            button_save_menu: Gtk.Menu = Gtk.Menu()
            option_save_all_tabs: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Save all configured")
            option_save_all_tabs.set_has_tooltip(has_tooltip=True)
            option_save_all_tabs.set_tooltip_markup(markup="Save all configured tabs to configuration file")
            option_save_all_tabs.connect("activate", lambda gtk_menu_item: self.save_configured_tabs())

            button_save_menu.append(child=self.option_save_current_tab)
            button_save_menu.append(child=option_save_all_tabs)
            button_save_menu.show_all()

            button_save: Gtk.Button = cast(Gtk.Button, self._gui.builder.get_object(name="buttonSaveConfigNotebook"))
            button_save.set_has_tooltip(has_tooltip=True)
            button_save.set_tooltip_markup(markup="Save the configuration of the current tab or all tabs")
            button_save.connect("clicked", lambda gtk_button: button_save_menu.popup_at_pointer(trigger_event=None))

            def cb_notebook_switch_page(page_num: int) -> None:
                """
                Callback when the Gtk.Notebook changes page
                """
                tab_associated: GUI.Tab = self.tab_list[page_num]
                self.option_save_current_tab.set_has_tooltip(has_tooltip=tab_associated.paired_folder.is_valid)
                self.option_save_current_tab.set_sensitive(sensitive=tab_associated.paired_folder.is_valid)

            cb_notebook_switch_page(page_num=0)
            self.notebook.connect(
                "switch-page", lambda gtk_notebook, child_widget, page_num: cb_notebook_switch_page(page_num=page_num)
            )

        def setup_tabs_from_config(self) -> None:
            for index, uuid_paired_folder in enumerate(iterable=self._gui._application.paired_folders_app):
                self.add_tab(
                    uuid_paired_folder=uuid_paired_folder,
                    has_close_button=False if index == 0 else True,
                    focus_new_tab=False,
                )
            if not len(self.notebook.get_children()):
                self.add_tab(has_close_button=False)

        def add_tab(
            self, uuid_paired_folder: Optional[str] = None, has_close_button: bool = True, focus_new_tab: bool = True
        ) -> None:
            """
            Add a new tab to the Notebook
            """
            new_tab: GUI.Tab = GUI.Tab(
                parent=self,
                builder=self._gui._application.get_new_builder(),
                uuid_paired_folder=uuid_paired_folder,
                include_close_button=has_close_button,
            )

            self.notebook.append_page(child=new_tab.container, tab_label=new_tab.title_container)
            self.tab_list.append(new_tab)
            if focus_new_tab:
                self.notebook.set_current_page(page_num=-1)

        def delete_tab(self, tab: GUI.Tab) -> None:
            """
            Delete tab from Gtk.Notebook
            """

            def cb_delete_tab(tab: GUI.Tab) -> None:
                self.notebook.detach_tab(child=tab.container)
                self.tab_list.remove(tab)
                self._gui._application.emit("app-delete-paired-folder", tab._uuid_paired_folder)
                self._gui._application.emit("appindicator-delete-paired-folder", tab._uuid_paired_folder)

                # Reorganize tabs
                for index, tab in enumerate(iterable=self.tab_list):
                    if "NewPairedFolder" in tab.label_tab.get_text():
                        new_alias: str = f"NewPairedFolder{index+1}"
                        tab.label_tab.set_markup(str=new_alias)
                        self._gui._application.emit(
                            "app-update-paired-folder", tab._uuid_paired_folder, "alias", new_alias
                        )

            if tab.folder_observer is not None and tab.folder_observer.running:
                dialog: Gtk.MessageDialog = self._gui._application.create_dialog(
                    parent=self._gui.window,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.YES_NO,
                    text="Stop synchronization",
                    text2="The synchronization is currently active, do you want to stop it?",
                )
                self._gui._application._block_gui = True
                response: int = dialog.run()
                dialog.destroy()
                self._gui._application._block_gui = False
                if response == Gtk.ResponseType.YES:
                    cb_delete_tab(tab=tab)
            else:
                cb_delete_tab(tab=tab)

        def show_tab(self, tab_uuid: str) -> None:
            """
            Focus tab that contains the specified `tab_uuid`
            """
            page_num: int = -1
            for tab in self.tab_list:
                if tab._uuid_paired_folder == tab_uuid:
                    page_num = self.notebook.page_num(child=tab.container)
                    break
            if page_num != -1:
                self.notebook.set_current_page(page_num=page_num)

        def save_configured_tabs(self) -> None:
            """
            Save all valid tabs (which source and target are chosen) in the configuration file
            """
            dialog: Gtk.MessageDialog = self._gui._application.create_dialog(
                parent=self._gui.window,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Save configuration",
                text2="Do you want to save all configured tabs (those which source and target are chosen) in the configuration file?\n\n<b>Note:</b> This will create new sections in the configuration file containing all new configured tabs.",
            )
            i: int = 0
            valid_tabs: List[GUI.Tab] = []
            for tab in self.tab_list:
                if tab.paired_folder.is_valid and tab.paired_folder.has_changed():
                    valid_tabs.append(tab)
                    i += 1
            if not i >= 1:
                dialog = self._gui._application.create_dialog(
                    parent=self._gui.window,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Save configuration",
                    text2=f"All currently configured tabs are already saved in the configuration file.",
                )
            self._gui._application._block_gui = True
            response_id: int = dialog.run()
            dialog.destroy()
            if response_id == Gtk.ResponseType.YES:
                result: bool = self._gui._application.app_save_config()
                text_positive: str = "The configuration file has been updated successfully. "
                text_positive += "New sections were added with the configuration of all valid tabs.\n\n"
                text_positive += "<b>New sections:\n</b>"
                for tab in valid_tabs:
                    text_positive += f'    <b>-</b> "{tab.paired_folder.alias}"\n'
                text: str = text_positive if result else "An error occurred saving the configuration to file."
                dialog = self._gui._application.create_dialog(
                    parent=self._gui.window,
                    message_type=Gtk.MessageType.INFO if result else Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Save configuration",
                    text2=text,
                )
                if result:
                    for tab in valid_tabs:
                        tab.paired_folder.update_config_after_save()
                        tab.button_delete_config.set_visible(visible=True)
                        tab.show_text_textview(mode="save-config")
                    self._gui.show_link_open_config_file()
                dialog.run()
                dialog.destroy()
            self._gui._application._block_gui = False

        def start_stop_tab_sync(self, tab_uuid: str, start: bool) -> None:
            """
            Start/stop a specific tab synchronization
            """
            tab_: Optional[GUI.Tab] = None
            for tab in self.tab_list:
                if tab._uuid_paired_folder == tab_uuid:
                    tab_ = tab
                    break
            if tab_ is None:
                return
            tab_.start_stop_sync(start=start)

        def stop_observers(self) -> None:
            """
            Stop all the active observers
            """
            for tab in self.tab_list:
                if tab.folder_observer is not None:
                    tab.folder_observer.stop()

        def show_info_tab_textview(self, tab_uuid: str, info: Dict[str, str]) -> None:
            """
            Show event on specific tab's Textview
            """
            tab: Optional[GUI.Tab] = None
            for tab_ in self.tab_list:
                if tab_._uuid_paired_folder == tab_uuid:
                    tab = tab_
                    break
            if tab is not None:
                tab.show_event_textview(info=info)

    def __init__(self, application: Application, builder: Gtk.Builder) -> None:
        self._application: Application = application
        self.builder: Gtk.Builder = builder
        self.window: Gtk.Window = cast(Gtk.Window, self.builder.get_object(name="mainWindow"))
        self.label_open_config_file: Gtk.Label = cast(Gtk.Label, self.builder.get_object(name="linkOpenConfigFile"))
        self._notebook: GUI.Notebook = GUI.Notebook(parent=self)
        self.setup_ui()

    def setup_ui(self) -> None:
        self.window.set_application(application=self._application)
        self.window.set_title(title=global_title)
        self.window.set_icon(icon=GdkPixbuf.Pixbuf.new_from_file(filename=str(icon)))

        header_bar: Gtk.HeaderBar = cast(Gtk.HeaderBar, self.builder.get_object(name="headerBar"))
        icon_header_bar: Gtk.Widget = Gtk.Image.new_from_pixbuf(
            pixbuf=GdkPixbuf.Pixbuf.new_from_file_at_scale(
                filename=str(icon), width=28, height=28, preserve_aspect_ratio=True
            )
        )
        header_bar.add(widget=icon_header_bar)

        self.show_link_open_config_file()

        hide_window_button: Gtk.Button = cast(Gtk.Button, self.builder.get_object(name="hideWindowButton"))
        hide_window_button.connect("clicked", lambda gtk_button: self.window.hide())

        exit_button: Gtk.Button = cast(Gtk.Button, self.builder.get_object(name="exitButton"))
        exit_button.connect("clicked", lambda gtk_button: self._application.exit_())

        self.connect_()
        if not "--hidden" in sys.argv:
            self.window.show_all()

    def connect_(self) -> None:
        self.window.connect("delete-event", lambda gtk_window, gdk_event: self.window.hide_on_delete())

    def show_link_open_config_file(self) -> None:
        self.label_open_config_file.set_markup(str=f'<a href="file://{str(config_file)}">Open configuration file</a>')
        self.label_open_config_file.set_visible(visible=config_file.is_file())


class AppIndicator(GObject.Object):
    """
    Class that handles the appindicator
    """

    def __init__(self, application: Application) -> None:
        super().__init__()
        self._application: Application = application
        self.indicator: AppIndicator3.Indicator = AppIndicator3.Indicator.new(
            id="synchronizer-for-linux-indicator",
            icon_name=str(icon),
            category=AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator_menu: Gtk.Menu = Gtk.Menu()
        self.paired_folders: Dict[str, Dict[str, Union[Gtk.MenuItem, Dict[str, Gtk.MenuItem]]]] = {}
        self.setup_indicator()

    def setup_indicator(self) -> None:
        self.indicator.set_title(title=global_title)
        self.indicator.set_status(status=AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(menu=self.indicator_menu)

        # Menu
        open_main_window: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Open main window")
        open_main_window.connect("activate", lambda gtk_menu_item: self._application.emit("gui-show-main-window"))

        exit_: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Exit")
        exit_.connect("activate", lambda gtk_menu_item: self._application.exit_())

        self.indicator_menu.append(child=open_main_window)
        self.indicator_menu.append(child=exit_)
        self.indicator_menu.show_all()
        self.add_paired_folders_from_config()

    def add_paired_folders_from_config(self) -> None:
        """
        Adds new subitems to the appindicator based on the configuration read from the config file
        """
        for uuid_, paired_folder in self._application.paired_folders_app.items():
            self.add_new_paired_folder(paired_folder=paired_folder, uuid_=uuid_)

    def add_new_paired_folder(self, paired_folder: PairedFolder, uuid_: str) -> None:
        """
        Add a new paired folder as a subitem to the appindicator

        :param PairedFolder paired_folder: New PairedFolder to add

        :patam str uuid_: uuid of new PairedFolder
        """
        if uuid_ in self.paired_folders:
            return

        if not len(self.paired_folders):
            paired_folders: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="--Paired Folders--")
            paired_folders.set_sensitive(sensitive=False)
            self.indicator_menu.insert(child=paired_folders, position=1)
            self.indicator_menu.insert(child=Gtk.SeparatorMenuItem.new(), position=2)

        def create_submenu() -> Dict[str, Union[Gtk.MenuItem, Dict[str, Gtk.MenuItem]]]:
            submenu_paired_foders: Gtk.Menu = Gtk.Menu()

            # Submenu
            status: Gtk.MenuItem = Gtk.MenuItem.new_with_label(
                label=f"Sync status: {'ACTIVE' if paired_folder._synchronization_status else 'INACTIVE'}"
            )
            status.set_sensitive(sensitive=False)
            submenu_paired_foders.append(child=status)

            alias: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label=f"Alias: {paired_folder.alias}")
            alias.set_sensitive(sensitive=False)
            submenu_paired_foders.append(child=alias)

            show_tab: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Show tab")
            show_tab.connect("activate", lambda gtk_menu_item: self._application.emit("gui-show-tab", uuid_))
            submenu_paired_foders.append(child=show_tab)

            start_sync: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Start synchronization")
            start_sync.connect(
                "activate", lambda gtk_menu_item: self._application.emit("app-start-stop-sync", uuid_, True)
            )
            submenu_paired_foders.append(child=start_sync)

            stop_sync: Gtk.MenuItem = Gtk.MenuItem.new_with_label(label="Stop synchronization")
            stop_sync.connect(
                "activate", lambda gtk_menu_item: self._application.emit("app-start-stop-sync", uuid_, False)
            )
            stop_sync.set_sensitive(sensitive=False)
            submenu_paired_foders.append(child=stop_sync)

            new_paired_folder.set_submenu(submenu=submenu_paired_foders)
            return {
                "submenu": new_paired_folder,
                "options": {"status": status, "alias": alias, "start_sync": start_sync, "stop_sync": stop_sync},
            }

        new_paired_folder: Gtk.MenuItem = Gtk.MenuItem.new_with_label(
            label=f"Paired Folder #{len(self.paired_folders)+1}"
        )
        dict_menu: Dict[str, Union[Gtk.MenuItem, Dict[str, Gtk.MenuItem]]] = create_submenu()
        self.paired_folders[uuid_] = dict_menu

        new_position = len(self.indicator_menu.get_children()) - 2
        self.indicator_menu.insert(child=new_paired_folder, position=new_position)
        self.indicator_menu.show_all()

    def delete_paired_folder(self, tab_uuid: str) -> None:
        """
        Delete paired folder from appindicator
        """
        if tab_uuid in self.paired_folders:
            menu_item: Gtk.MenuItem = cast(Gtk.MenuItem, self.paired_folders[tab_uuid]["submenu"])
            self.indicator_menu.remove(widget=menu_item)
            del self.paired_folders[tab_uuid]
            self.reorganize_items()

    def reorganize_items(self) -> None:
        """
        Reorganize menu items
        """
        for index, uuid in enumerate(iterable=self.paired_folders):
            cast(Gtk.MenuItem, self.paired_folders[uuid]["submenu"]).set_label(label=f"Paired Folder #{index + 1}")

    def update_alias_or_create_item(self, tab_uuid: str, paired_folder: PairedFolder) -> None:
        """
        Update alias of a PairedFolder or create new submenu if the PairedFolder is not in the appindicator
        """
        if tab_uuid not in self.paired_folders:
            self.add_new_paired_folder(paired_folder=paired_folder, uuid_=tab_uuid)
            return
        alias_item: Gtk.MenuItem = cast(Dict[str, Gtk.MenuItem], self.paired_folders[tab_uuid]["options"])["alias"]
        alias_item.set_label(label=paired_folder.alias)

    def update_item_based_on_sync(self, tab_uuid: str) -> None:
        """
        Update submenu's options based if the synchronization has been started or stopped
        """
        if tab_uuid not in self.paired_folders:
            return

        paired_folder: PairedFolder = self._application.paired_folders_app[tab_uuid]
        status_item: Gtk.MenuItem = cast(Dict[str, Gtk.MenuItem], self.paired_folders[tab_uuid]["options"])["status"]
        status_item.set_label(label=f"Sync status: {'ACTIVE' if paired_folder._synchronization_status else 'INACTIVE'}")

        start_sync_item: Gtk.MenuItem = cast(Dict[str, Gtk.MenuItem], self.paired_folders[tab_uuid]["options"])[
            "start_sync"
        ]
        start_sync_item.set_sensitive(sensitive=not paired_folder._synchronization_status)

        stop_sync_item: Gtk.MenuItem = cast(Dict[str, Gtk.MenuItem], self.paired_folders[tab_uuid]["options"])[
            "stop_sync"
        ]
        stop_sync_item.set_sensitive(sensitive=paired_folder._synchronization_status)


class PairedFolder:
    """
    Class used to manage every pair of synched folders
    """

    def __init__(
        self,
        alias: str,
        source: str = "",
        target: str = "",
        buffer_size: int = 1000,
        include_hidden_files: bool = False,
        autostart_sync: bool = False,
        is_config_saved: bool = False,
    ) -> None:
        self.alias: str = alias
        self.buffer_size: int = buffer_size
        self.include_hidden_files: bool = include_hidden_files
        self.autostart_sync: bool = autostart_sync
        self._synchronization_status: bool = False
        self._source: Optional[Path] = Path(source) if len(source) else None
        self._target: Optional[Path] = Path(target) if len(target) else None
        self._is_config_saved: bool = is_config_saved
        self._original_state: Dict[str, Union[str, int, bool, Path]] = self.__dict__.copy()

    @property
    def source(self) -> Path:
        return cast(Path, self._source)

    @property
    def target(self) -> Path:
        return cast(Path, self._target)

    @property
    def is_valid(self) -> bool:
        return self._source is not None and self._target is not None

    def validate_from_config(self, valid_paired_folders: Dict[str, PairedFolder]) -> List[str]:
        """
        Checks if the PairedFolder is valid and returns the list of errors if there's any
        """

        def check_paths(path: Optional[Path], opposite_path: Optional[Path], type: str, mode: int) -> None:
            if path is None or not path.is_dir():
                messages.append(f"The {type} path is not a valid location")
                return

            if not os.access(path=path, mode=mode):
                messages.append(f"The {type} path is not a {'readable' if type == 'source' else 'writable'} location")
                return

            if opposite_path is not None and (path.resolve() == opposite_path.resolve()):
                messages.append("Source and target point to the same location")
                return

            if type == "source":
                return

            for _, value in valid_paired_folders.items():
                if path.resolve() == value.target.resolve():
                    messages.append(f'The {type} path is already in use by the configuration "{value.alias}"')
                    return

        messages: List[str] = []

        ## SOURCE
        check_paths(path=self._source, opposite_path=self._target, type="source", mode=os.R_OK)

        ## TARGET
        check_paths(path=self._target, opposite_path=self._source, type="target", mode=os.W_OK)

        if self.buffer_size > max_buffer_size:
            messages.append(f"The maximum allowed buffer size is {max_buffer_size}")
        elif self.buffer_size < min_buffer_size:
            messages.append(f"The minimum allowed buffer size is {min_buffer_size}")

        return messages

    def has_changed(self) -> bool:
        """
        Check if there's any change on the PairedFolder
        """
        if not self._is_config_saved:
            return False

        dict_: Dict[str, Union[str, int, bool, Path]] = self.__dict__.copy()
        del dict_["_original_state"]
        return dict_ != self._original_state

    def build_json(self, original_state: bool = False) -> Dict[str, Union[str, Dict[str, Union[bool, int]]]]:
        """
        Build json config representing the PairedFolder
        """
        source: str = str(self.source)
        target: str = str(self.target)
        include_hidden_files: bool = self.include_hidden_files
        buffer_size: int = self.buffer_size
        autostart_sync: bool = self.autostart_sync
        if original_state:
            source = str(self._original_state["_source"])
            target = str(self._original_state["_target"])
            include_hidden_files = cast(bool, self._original_state["include_hidden_files"])
            buffer_size = cast(int, self._original_state["buffer_size"])
            autostart_sync = cast(bool, self._original_state["autostart_sync"])
        return {
            "source": source,
            "target": target,
            "options": {
                "include_hidden_files": include_hidden_files,
                "buffer_size": buffer_size,
                "autostart_sync": autostart_sync,
            },
        }

    def update_config_after_save(self, config_saved: bool = True) -> None:
        """
        Update PairedFolder configuration after it has being saved
        """
        self._is_config_saved = config_saved
        self._original_state = self.__dict__.copy()
        del self._original_state["_original_state"]


class FolderObserver:
    """
    Core class that handles the observer of a paired folder
    """

    class EventHandler(we.FileSystemEventHandler):
        """
        Class that handles all the events actions, mimics all events reported on source in target folder
        """

        class EventLocation:
            """
            Auxiliar class that saves a base, internal and external path.

            The base path represents the path chosen as is

            The internal path is the combination of base path + new parts (where the event happened) to form
            a new resolved path, the one used to replicate events

            The external path is the combination of base path + new parts (where the event happened) to form
            a new unresolved path, the one shown in the textview
            """

            def __init__(self, path: Path) -> None:
                self.base_path: Path = path
                self.internal_path: Path = path
                self.external_path: Path = path

        def __init__(self, folder_observer: FolderObserver, paired_folder: PairedFolder) -> None:
            super().__init__()

            self._folder_observer: FolderObserver = folder_observer
            self.include_hidden_files: bool = paired_folder.include_hidden_files

            self.source_location = FolderObserver.EventHandler.EventLocation(path=paired_folder.source)
            self.target_location = FolderObserver.EventHandler.EventLocation(path=paired_folder.target)

        def on_any_event(self, event: we.FileSystemEvent) -> None:
            """
            All events reported are handled here
            """
            event_path = Path(event.src_path)
            new_parts = event_path.parts[len(self.source_location.base_path.resolve().parts) :]

            # Source's internal path (resolved)
            self.source_location.internal_path = event_path

            # Source's external path
            self.source_location.external_path = self.source_location.base_path.joinpath(*new_parts)

            # Target's internal path (resolved)
            self.target_location.internal_path = self.target_location.base_path.resolve().joinpath(*new_parts)

            # Target's external path
            self.target_location.external_path = self.target_location.base_path.joinpath(*new_parts)

        def on_moved(self, event: Union[we.DirMovedEvent, we.FileMovedEvent]) -> None:
            info_textview: Dict[str, str] = {"Event": f"{'Folder' if event.is_directory else 'File'} movement"}
            try:
                # Ignore hidden files if the option is disabled
                if not self.include_hidden_files and any(
                    path.startswith(".") for path in self.source_location.internal_path.parts
                ):
                    return

                # Destination of movement in the source folder
                internal_target_source: Path = Path(event.dest_path)
                new_parts_source = internal_target_source.parts[len(self.source_location.base_path.resolve().parts) :]
                external_target_source: Path = self.source_location.base_path.joinpath(*new_parts_source)

                # Destination of movement in the target folder
                internal_target_target = self.target_location.base_path.resolve().joinpath(*new_parts_source)
                external_target_target = self.target_location.base_path.joinpath(*new_parts_source)

                info_textview.update(
                    {
                        "Source": f"{str(self.source_location.external_path)} --> {str(external_target_source)}",
                        "Target": f"{str(self.target_location.external_path)} --> {str(external_target_target)}",
                    }
                )

                ## Event Replication
                if self.target_location.internal_path.exists():
                    self.target_location.internal_path.rename(target=internal_target_target)

                info_textview.update({"Result": f"{'Folder' if event.is_directory else 'File'} moved correctly"})
            except Exception as e:
                info_textview.update(
                    {"Result": f"An error occurred moving the {'folder' if event.is_directory else 'file'}"}
                )
                print(f"EXCEPTION: {e}")
            finally:
                self.emit_event_to_textview(info=info_textview)

        def on_created(self, event: Union[we.DirCreatedEvent, we.FileCreatedEvent]) -> None:
            info_textview: Dict[str, str] = {"Event": f"{'Folder' if event.is_directory else 'File'} creation"}
            try:
                # Ignore hidden files if the option is disabled
                if not self.include_hidden_files and any(
                    path.startswith(".") for path in self.source_location.internal_path.parts
                ):
                    return

                info_textview.update(
                    {
                        "Source": str(self.source_location.external_path),
                        "Target": str(self.target_location.external_path),
                    }
                )

                ## Event Replication
                if self.source_location.internal_path.exists():
                    if event.is_directory:
                        self.target_location.internal_path.mkdir(exist_ok=True)
                    else:
                        shutil.copy2(src=self.source_location.internal_path, dst=self.target_location.internal_path)

                info_textview.update({"Result": f"{'Folder' if event.is_directory else 'File'} created successfully"})
            except Exception as e:
                info_textview.update(
                    {"Result": f"An error occurred creating the {'folder' if event.is_directory else 'file'}"}
                )
                print(f"EXCEPTION: {e}")
            finally:
                self.emit_event_to_textview(info=info_textview)

        def on_deleted(self, event: Union[we.DirDeletedEvent, we.FileDeletedEvent]) -> None:
            def rmtree(folder: Path) -> None:
                """
                Removes file or folder recursively
                """
                if folder.is_file():
                    folder.unlink()
                else:
                    for child in folder.iterdir():
                        rmtree(folder=child)
                    folder.rmdir()

            info_textview: Dict[str, str] = {"Event": f"{'Folder' if event.is_directory else 'File'} deletion"}
            try:
                # Ignore hidden files if the option is disabled
                if not self.include_hidden_files and any(
                    path.startswith(".") for path in self.source_location.internal_path.parts
                ):
                    return

                info_textview.update(
                    {
                        "Source": str(self.source_location.external_path),
                        "Target": str(self.target_location.external_path),
                    }
                )

                ## Event Replication
                if self.target_location.internal_path.exists():
                    rmtree(folder=self.target_location.internal_path)

                info_textview.update({"Result": f"{'Folder' if event.is_directory else 'File'} deleted successfully"})
            except Exception as e:
                info_textview.update(
                    {"Result": f"An error occurred deleting the {'folder' if event.is_directory else 'file'}"}
                )
                print(f"EXCEPTION: {e}")
            finally:
                self.emit_event_to_textview(info=info_textview)

        def on_closed(self, event: we.FileClosedEvent) -> None:
            info_textview: Dict[str, str] = {"Event": "File edition"}
            try:
                # Ignore hidden files if the option is disabled
                if not self.include_hidden_files and any(
                    path.startswith(".") for path in self.source_location.internal_path.parts
                ):
                    return

                info_textview.update(
                    {
                        "Source": str(self.source_location.external_path),
                        "Target": str(self.target_location.external_path),
                    }
                )

                ## Event Replication
                if self.source_location.internal_path.exists():
                    shutil.copy2(src=self.source_location.internal_path, dst=self.target_location.internal_path)

                info_textview.update({"Result": "File edited correctly"})
            except Exception as e:
                info_textview.update({"Result": "An error occured editing the file"})
                print(f"EXCEPTION: {e}")
            finally:
                self.emit_event_to_textview(info=info_textview)

        def emit_event_to_textview(self, info: Dict[str, str]) -> None:
            """
            Emit signal to the app to show info on textview
            """
            self._folder_observer.application.emit(
                "gui-show-info-textview", self._folder_observer.uuid_paired_folder, info
            )

    def __init__(self, application: Application, uuid_paired_folder: str) -> None:
        event_handler: FolderObserver.EventHandler = FolderObserver.EventHandler(
            folder_observer=self, paired_folder=application.paired_folders_app[uuid_paired_folder]
        )
        self.application: Application = application
        self.uuid_paired_folder: str = uuid_paired_folder
        self.observer: wo.Observer = wo.Observer()
        self.observer.schedule(
            event_handler=event_handler, path=str(event_handler.source_location.base_path.resolve()), recursive=True
        )
        self.running: bool = True
        try:
            self.observer.start()
        except:
            self.running = False

    def stop(self) -> None:
        if self.running:
            self.observer.stop()
            self.observer.join()
            self.running = False


# Globals
icon: Path = Path(__file__).parent.joinpath("synchronizer.png")
ui_file: Path = Path(__file__).parent.joinpath("synchronizer.xml")
ui_content: str = ""

# Config file
config_file: Path = (
    Path(os.environ["APPIMAGE"]).parent.joinpath("config.json")
    if "APPIMAGE" in os.environ
    else Path.cwd().joinpath("config.json")
)

# Constants
global_title: str = "Synchronizer for Linux"
max_buffer_size: int = 1000000
default_buffer_size: int = 1000
min_buffer_size: int = 100

# Schema for the json validation
json_schema: Dict[str, Union[str, Dict[str, Any], List[str], bool]] = {
    "type": "object",
    "properties": {
        "paired_folders": {
            "type": "object",
            "propertyNames": {"pattern": "^NewPairedFolder[1-9]+|.+ --> .+$"},
            "patternProperties": {
                "^NewPairedFolder[1-9]+|.+ --> .+$": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "options": {
                            "type": "object",
                            "properties": {
                                "include_hidden_files": {"type": "boolean"},
                                "buffer_size": {"type": "integer"},
                                "autostart_sync": {"type": "boolean"},
                            },
                            "additionalProperties": False,
                            "required": ["include_hidden_files", "buffer_size", "autostart_sync"],
                        },
                    },
                    "additionalProperties": False,
                    "required": ["source", "target", "options"],
                }
            },
        }
    },
    "additionalProperties": False,
    "required": ["paired_folders"],
}

if __name__ == "__main__":

    if not ui_file.is_file():
        print("Could not find the required ui file, exiting...")
        exit()
    else:
        with ui_file.open(mode="r") as ui_file_content:
            ui_content = ui_file_content.read()

    app = Application(application_id="gtk3.synchronizer-for-linux")
    app.run(argv=[x for x in sys.argv if x != "--hidden"])
