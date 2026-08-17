# src/endstone_warp_codes/warp_plugin.py
"""
Warp Code Manager Plugin for Endstone 0.11.9+
Complete 1:1 port from JavaScript implementation with custom duration support
"""

import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

import urllib.request
import threading
from endstone.plugin import Plugin
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerJoinEvent, PlayerChatEvent
from endstone.form import ActionForm, MessageForm, ModalForm, Dropdown, TextInput, Toggle
from endstone import ColorFormat, Player


class Database:
    """Simple JSON-based database with UUID support"""

    def __init__(self, plugin_folder: Path, name: str):
        self.file_path = plugin_folder / f"{name}.json"
        self.data = {}
        self.load()

    def _convert_uuid_to_str(self, obj):
        """Recursively convert UUID objects to strings for JSON serialization"""
        if isinstance(obj, UUID):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_uuid_to_str(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_uuid_to_str(item) for item in obj]
        else:
            return obj

    def load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r') as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"Error loading {self.file_path}: {e}")
                self.data = {}

    def save(self):
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            # Convert UUIDs to strings before saving
            serializable_data = self._convert_uuid_to_str(self.data)
            with open(self.file_path, 'w') as f:
                json.dump(serializable_data, f, indent=2)
        except Exception as e:
            print(f"Error saving {self.file_path}: {e}")

    def read(self, key) -> Optional[dict]:
        # Convert UUID key to string if needed
        str_key = str(key) if isinstance(key, UUID) else key
        return self.data.get(str_key)

    def write(self, key, value: dict):
        # Convert UUID key to string if needed
        str_key = str(key) if isinstance(key, UUID) else key
        # Convert UUID values to strings
        serializable_value = self._convert_uuid_to_str(value)
        self.data[str_key] = serializable_value
        self.save()

    def delete(self, key):
        # Convert UUID key to string if needed
        str_key = str(key) if isinstance(key, UUID) else key
        if str_key in self.data:
            del self.data[str_key]
            self.save()

    def has(self, key) -> bool:
        # Convert UUID key to string if needed
        str_key = str(key) if isinstance(key, UUID) else key
        return str_key in self.data

    def keys(self) -> List[str]:
        return list(self.data.keys())


class CodeType:
    def __init__(self, tag: str, display_name: str):
        self.tag = tag
        self.display_name = display_name


class WarpCodeManager(Plugin):
    api_version = "0.11"

    # Define commands as class attribute (dict format)
    commands = {
        "codes": {
            "description": "Manage warp codes",
            "usages": ["/codes"],
            "aliases": ["warp", "warpcodes"],
            "permissions": ["warp.admin"]
        },
        "redeem": {
            "description": "Redeem a warp code",
            "usages": ["/redeem"]
        },
        "viewall": {
            "description": "View all players' active codes",
            "usages": ["/viewall"],
            "permissions": ["warp.admin"]
        },
        "stats": {
            "description": "Show warp code statistics",
            "usages": ["/stats"],
            "permissions": ["warp.admin"]
        },
        "clearunredeemed": {
            "description": "Clear all unredeemed codes",
            "usages": ["/clearunredeemed"],
            "permissions": ["warp.admin"]
        },
        "timeleft": {
            "description": "Check your active codes",
            "usages": ["/timeleft"]
        },
        "toc": {
            "description": "Transfer build tag to another player",
            "usages": ["/toc <player>"]
        },
        "generate": {
            "description": "Generate codes (fallback)",
            "usages": ["/generate"],
            "permissions": ["warp.admin"]
        },
        "myhistory": {
            "description": "View your code redemption history",
            "usages": ["/myhistory"]
        },
        "updateplayernames": {
            "description": "Update player names in database",
            "usages": ["/updateplayernames"],
            "permissions": ["warp.admin"]
        },
        "fixtags": {
            "description": "Fix tags for all players (reactivate valid codes)",
            "usages": ["/fixtags"],
            "permissions": ["warp.admin"]
        }
    }

    # Define permissions as class attribute (dict format)
    permissions = {
        "warp.admin": {
            "description": "Allows access to code management",
            "default": "op"
        }
    }

    CODE_LENGTH = 8
    MAX_CODES = 50
    MIN_DAYS = 1
    MAX_DAYS = 3650
    CHECK_INTERVAL = 6000  # 5 minutes (6000 ticks = 300 seconds = 5 minutes)

    # Built-in code types that always exist and cannot be deleted
    BUILTIN_CODE_TYPES = {
        'BASIC': CodeType('WARP1', 'Basic'),
        'ENHANCED': CodeType('WARP2', 'Enhanced'),
        'WARP3': CodeType('WARP3', 'Epic'),
        'BUILD': CodeType('build', 'Build')
    }

    def __init__(self):
        super().__init__()
        self.code_db = None
        self.player_db = None
        self.type_db = None

        self.CODE_TYPES = dict(self.BUILTIN_CODE_TYPES)
        self.TAG_TO_TYPE = {ct.tag: ct for ct in self.CODE_TYPES.values()}

    def on_load(self):
        self.logger.info("Loading Warp Code Manager...")

    def on_enable(self):
        self.code_db = Database(self.data_folder, "warp_codes")
        self.player_db = Database(self.data_folder, "warp_players")
        self.type_db = Database(self.data_folder, "code_types")
        self.webhook_db = Database(self.data_folder, "webhooks")
        # Stores Discord message IDs so we can edit them when codes are redeemed
        self.webhook_messages_db = Database(self.data_folder, "webhook_messages")

        if not self.webhook_db.has("webhooks"):
            default_config = {}
            for tag in self.TAG_TO_TYPE.keys():
                default_config[tag] = {"default": ""}
            self.webhook_db.write("webhooks", default_config)

        # Load custom code types from database
        self.reload_code_types()

        # Migrate existing codes to new format if needed
        self.migrate_database()

        # Schedule periodic checks
        def schedule_check():
            self.check_all_players()
            self.server.scheduler.run_task(self, schedule_check, delay=self.CHECK_INTERVAL)

        self.server.scheduler.run_task(self, schedule_check, delay=20)

        # Log command registration for debugging
        self.logger.info("Warp Code Manager enabled!")
        self.logger.info("Admin commands: /codes, /warp, /warpcodes, /viewall, /stats, /clearunredeemed, /generate, /updateplayernames, /fixtags")
        self.logger.info("Player commands: /redeem, /timeleft, /toc, /myhistory")
        self.logger.info("Multi-use code feature and time stacking are now available!")
        self.logger.info("Expiration checks run every 5 minutes with immediate checks on join and command usage")
        custom_count = len(self.CODE_TYPES) - len(self.BUILTIN_CODE_TYPES)
        if custom_count > 0:
            self.logger.info(f"Loaded {custom_count} custom code type(s)")

    def on_disable(self):
        self.logger.info("Warp Code Manager disabled!")

    def reload_webhooks(self, player=None):
        self.webhook_db.load()
        if player:
            player.send_message(f"{ColorFormat.GREEN}Reloaded webhooks configuration.")
        else:
            self.logger.info("Reloaded webhooks configuration.")

    def reload_code_types(self):
        """Reload CODE_TYPES from built-in defaults + custom types from database"""
        self.CODE_TYPES = dict(self.BUILTIN_CODE_TYPES)

        for key in self.type_db.keys():
            type_data = self.type_db.read(key)
            if type_data:
                tag = type_data.get('tag', '')
                display_name = type_data.get('display_name', '')
                if tag and display_name:
                    self.CODE_TYPES[key] = CodeType(tag, display_name)

        self.TAG_TO_TYPE = {ct.tag: ct for ct in self.CODE_TYPES.values()}

    def migrate_database(self):
        """Migrate existing codes to support multi-use functionality"""
        migrated_count = 0

        for code_key in self.code_db.keys():
            code_data = self.code_db.read(code_key)
            if code_data and 'isMultiUse' not in code_data:
                # This is an existing code, mark it as single-use
                code_data['isMultiUse'] = False
                self.code_db.write(code_key, code_data)
                migrated_count += 1

        if migrated_count > 0:
            pass  # Migration complete

    def on_command(self, sender: CommandSender, command: Command, args: List[str]) -> bool:
        """Handle all plugin commands"""

        if command.name == "codes" or command.name in ["warp", "warpcodes"]:
            if len(args) > 0 and args[0].lower() == "reload":
                if not sender.has_permission("warp.admin"):
                    sender.send_error_message(f"{ColorFormat.RED}No permission!")
                    return True
                self.webhook_db.load()
                sender.send_message(f"{ColorFormat.GREEN}Reloaded webhooks configuration!")
                return True

            if not isinstance(sender, Player):
                # Console usage - provide help text
                sender.send_message("Code management is only available through the in-game UI.")
                sender.send_message("Use '/codes' as an operator in-game to access the management interface.")
                sender.send_message("Available commands for players: /redeem")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.show_main_menu(player)
            return True

        elif command.name == "redeem":
            if not isinstance(sender, Player):
                sender.send_message("Code redemption is only available for players in-game.")
                return True

            player = sender
            self.show_redeem_form(player)
            return True

        elif command.name == "viewall":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.view_all_player_times(player)
            return True

        elif command.name == "stats":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.show_statistics(player)
            return True

        elif command.name == "clearunredeemed":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.show_clear_unredeemed_confirmation(player)
            return True

        elif command.name == "timeleft":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            self.check_and_show_player_time(player)
            return True

        elif command.name == "toc":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if len(args) < 1:
                player.send_error_message(f"{ColorFormat.RED}Usage: /toc <player_name>")
                return True

            target_name = " ".join(args)  # Handle names with spaces
            self.transfer_build_tag(player, target_name)
            return True

        elif command.name == "generate":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.show_generate_type_menu(player)
            return True

        elif command.name == "myhistory":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            self.show_player_history(player)
            return True

        elif command.name == "updateplayernames":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.update_all_player_names(player)
            return True

        elif command.name == "fixtags":
            if not isinstance(sender, Player):
                sender.send_message("This command is only available for players in-game.")
                return True

            player = sender
            if not player.has_permission("warp.admin"):
                player.send_error_message(f"{ColorFormat.RED}You don't have permission! You need operator status or warp.admin permission.")
                return True

            self.fix_all_player_tags(player)
            return True

        return False

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        player = event.player
        self.server.scheduler.run_task(self, lambda: self.check_and_update_player(player))

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        """Check and update player codes immediately when they join"""
        player = event.player
        # Schedule immediate check after a short delay to ensure player is fully loaded
        self.server.scheduler.run_task(self, lambda: self.safe_check_and_update_player(player), delay=20)

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        player = event.player
        message = event.message.strip()

        if message.startswith("-toc ") and self.player_has_tag(player, "build"):
            event.cancelled = True
            target_name = message[5:].strip()
            self.server.scheduler.run_task(self, lambda: self.transfer_build_tag(player, target_name))

        elif message == "-time":
            event.cancelled = True
            # First update the player's codes, then show the time
            self.server.scheduler.run_task(self, lambda: self.check_and_show_player_time(player))

        elif message == "-viewall" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.view_all_player_times(player))

        elif message == "-updateplayernames" and self.player_has_tag(player, "build"):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.update_all_player_names(player))

        elif message == "-clearunredeemedcodes" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.show_clear_unredeemed_confirmation(player))

        elif message == "-stats" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.show_statistics(player))

        elif message == "-myhistory":
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.show_player_history(player))

        elif message == "-help" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.show_admin_help(player))

        elif message == "-reload" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.reload_webhooks(player))

        elif message == "-generate" and (self.player_has_tag(player, "Admin") or player.has_permission("warp.admin")):
            event.cancelled = True
            self.server.scheduler.run_task(self, lambda: self.show_generate_type_menu(player))

    # UI Methods

    def show_main_menu(self, player):
        form = ActionForm()
        form.title = "Code Manager"
        form.content = "Select an action:"

        def on_submit(p, button_index):
            if button_index == 0:
                self.show_generate_type_menu(p)
            elif button_index == 1:
                self.show_view_menu(p)
            elif button_index == 2:
                self.show_edit_menu(p)
            elif button_index == 3:
                self.show_manage_code_types_menu(p)
            elif button_index == 4:
                if self.player_has_tag(p, "Admin") or p.has_permission("warp.admin"):
                    self.show_admin_menu(p)
                else:
                    p.send_error_message(f"{ColorFormat.RED}No admin permission!")

        form.on_submit = on_submit
        form.add_button("Generate Codes")
        form.add_button("View Codes")
        form.add_button("Edit Codes")
        form.add_button("Manage Code Types")
        form.add_button("Admin Tools")

        player.send_form(form)

    def show_admin_menu(self, player):
        form = ActionForm()
        form.title = "Admin Tools"
        form.content = "Select an admin tool:"
        
        def on_submit(p, button_index):
            if button_index == 0:
                self.show_clear_unredeemed_confirmation(p)
            elif button_index == 1:
                self.show_statistics(p)
            elif button_index == 2:
                self.reload_webhooks(p)
                
        form.on_submit = on_submit
        form.add_button("Clear Unredeemed Codes")
        form.add_button("View Statistics")
        form.add_button("Reload Webhooks Config")
        
        player.send_form(form)

    def show_generate_type_menu(self, player):
        form = ActionForm()
        form.title = "Generate Codes"
        form.content = "Select code type:"

        # Build types list dynamically from all registered code types
        types_list = list(self.CODE_TYPES.values())

        def on_submit(p, button_index):
            if 0 <= button_index < len(types_list):
                self.show_custom_duration_input(p, types_list[button_index])

        form.on_submit = on_submit
        for ct in types_list:
            form.add_button(f"{ct.display_name} Codes ({ct.tag})")

        player.send_form(form)

    def show_custom_duration_input(self, player, code_type: CodeType):
        duration_input = TextInput(
            label="duration",
            placeholder=f"Duration in days ({self.MIN_DAYS}-{self.MAX_DAYS})",
            default_value="30"
        )
        count_input = TextInput(
            label="count",
            placeholder=f"Number of codes (1-{self.MAX_CODES})",
            default_value="1"
        )
        multiuse_input = Dropdown(
            label="Code Type",
            options=["Single-use (traditional)", "Multi-use (redeemable by multiple players)"],
            default_index=0
        )
        webhook_toggle = Toggle(
            label="Send to Discord Webhook",
            default_value=False
        )

        def on_submit(p, json_data):
            try:
                data = json.loads(json_data) if isinstance(json_data, str) else json_data

                # Handle both dictionary and list formats
                if isinstance(data, list):
                    # For ModalForm with controls, data comes as a list
                    # Order: [duration_input, count_input, multiuse_input, webhook_toggle]
                    if len(data) < 4:
                        p.send_error_message(f"{ColorFormat.RED}Invalid form data!")
                        return
                    duration_str = str(data[0]).strip()
                    count_str = str(data[1]).strip()
                    is_multiuse = int(data[2]) == 1  # 1 = Multi-use, 0 = Single-use
                    send_webhook = bool(data[3])
                else:
                    # Dictionary format (fallback)
                    duration_str = data.get("duration", "").strip()
                    count_str = data.get("count", "").strip()
                    is_multiuse = data.get("multiuse", 0) == 1
                    send_webhook = bool(data.get("webhook", False))

                if not duration_str:
                    p.send_error_message(f"{ColorFormat.RED}Please enter a duration!")
                    return

                duration = int(duration_str)
                if duration < self.MIN_DAYS or duration > self.MAX_DAYS:
                    p.send_error_message(
                        f"{ColorFormat.RED}Duration must be between {self.MIN_DAYS} and {self.MAX_DAYS} days!")
                    return

                if not count_str:
                    p.send_error_message(f"{ColorFormat.RED}Please enter a count!")
                    return

                count = int(count_str)
                if count < 1 or count > self.MAX_CODES:
                    p.send_error_message(f"{ColorFormat.RED}Count must be between 1 and {self.MAX_CODES}!")
                    return

                self.generate_and_store_codes(p, code_type, count, duration, is_multiuse, send_webhook)

            except (ValueError, json.JSONDecodeError) as e:
                p.send_error_message(f"{ColorFormat.RED}Please enter valid numbers!")

        form = ModalForm(
            title=f"Generate {code_type.display_name} Codes",
            controls=[duration_input, count_input, multiuse_input, webhook_toggle],
            on_submit=on_submit
        )
        player.send_form(form)

    def show_view_menu(self, player):
        codes_by_tag = self.get_unredeemed_codes()

        def format_codes_with_info(codes):
            if not codes:
                return 'None'

            formatted = []
            for code in codes:
                code_data = self.code_db.read(code)
                if code_data:
                    is_multiuse = code_data.get('isMultiUse', False)
                    if is_multiuse:
                        redemption_count = len(code_data.get('redeemedBy', []))
                        formatted.append(f"{code} (Multi-use, {redemption_count} redeemed)")
                    else:
                        formatted.append(f"{code} (Single-use)")
                else:
                    formatted.append(code)
            return ', '.join(formatted)

        # Build body text dynamically from all code types
        sections = []
        for ct in self.CODE_TYPES.values():
            codes = codes_by_tag.get(ct.tag, [])
            sections.append(f"{ct.display_name} Codes ({ct.tag}):\n{format_codes_with_info(codes)}")

        body_text = '\n\n'.join(sections)

        form = MessageForm()
        form.title = "Unredeemed Codes"
        form.content = body_text
        form.button1 = "OK"

        player.send_form(form)

    def show_edit_menu(self, player):
        codes = []
        for key in self.code_db.keys():
            data = self.code_db.read(key)
            if data:
                # Include multi-use codes (always available) and unredeemed single-use codes
                is_multiuse = data.get('isMultiUse', False)
                is_available = is_multiuse or not data.get('redeemed', False)
                if is_available:
                    codes.append(key)

        if not codes:
            player.send_error_message(f"{ColorFormat.RED}No codes to delete.")
            return

        dropdown = Dropdown(label="code", options=codes)

        def on_submit(p, json_data):
            try:
                data = json.loads(json_data) if isinstance(json_data, str) else json_data

                # Handle both dictionary and list formats
                if isinstance(data, list):
                    # For ModalForm with Dropdown controls, data comes as a list
                    # Order: [dropdown]
                    if len(data) < 1:
                        p.send_error_message(f"{ColorFormat.RED}Invalid form data!")
                        return
                    idx = data[0]
                else:
                    # Dictionary format (fallback)
                    idx = data.get("code", 0)

                if isinstance(idx, int) and 0 <= idx < len(codes):
                    selected_code = codes[idx]
                    self.code_db.delete(selected_code)
                    p.send_message(f"{ColorFormat.GREEN}Deleted: {selected_code}")
            except (json.JSONDecodeError, Exception) as e:
                p.send_error_message(f"{ColorFormat.RED}Error deleting code")

        form = ModalForm(
            title="Delete Codes",
            controls=[dropdown],
            on_submit=on_submit
        )
        player.send_form(form)

    def show_redeem_form(self, player):
        code_input = TextInput(
            label="code",
            placeholder="Enter 8-digit code",
            default_value=""
        )

        def on_submit(p, json_data):
            try:
                data = json.loads(json_data) if isinstance(json_data, str) else json_data

                # Handle both dictionary and list formats
                if isinstance(data, list):
                    # For ModalForm with TextInput controls, data comes as a list
                    # Order: [code_input]
                    if len(data) < 1:
                        p.send_error_message(f"{ColorFormat.RED}Invalid form data!")
                        return
                    code = str(data[0]).strip()
                else:
                    # Dictionary format (fallback)
                    code = data.get("code", "").strip()

                self.process_redemption(p, code)
            except json.JSONDecodeError as e:
                p.send_error_message(f"{ColorFormat.RED}Invalid form data!")

        form = ModalForm(
            title="Redeem Code",
            controls=[code_input],
            on_submit=on_submit
        )
        player.send_form(form)

    def show_clear_unredeemed_confirmation(self, player):
        form = ActionForm()
        form.title = f"{ColorFormat.RED}Confirm Deletion"
        form.content = f"{ColorFormat.DARK_RED}WARNING:{ColorFormat.RESET} Delete all unredeemed codes? Cannot be undone!"

        def on_submit(p, button_index):
            if button_index == 0:
                self.clear_unredeemed_codes(p)
            else:
                p.send_message(f"{ColorFormat.YELLOW}Canceled.")

        form.on_submit = on_submit
        form.add_button(f"{ColorFormat.GREEN}Yes, Delete All")
        form.add_button(f"{ColorFormat.RED}Cancel")

        player.send_form(form)

    # Core Logic

    def generate_code(self) -> str:
        return ''.join([str(random.randint(0, 9)) for _ in range(self.CODE_LENGTH)])

    def generate_and_store_codes(self, player, code_type: CodeType, count: int, duration: int, is_multiuse: bool = False, send_webhook: bool = False):
        codes = []
        generation_time = time.time() * 1000

        for _ in range(count):
            while True:
                code = self.generate_code()
                if not self.code_db.has(code):
                    break

            code_data = {
                'type': code_type.tag,
                'typeName': code_type.display_name,
                'duration': duration,
                'generatedAt': generation_time,
                'generatedBy': player.name,
                'generatorUUID': player.unique_id,
                'isMultiUse': is_multiuse
            }

            if is_multiuse:
                # For multi-use codes, track which players have redeemed it
                code_data['redeemedBy'] = []  # List of player UUIDs who have redeemed this code
            else:
                # For single-use codes, use the traditional redeemed flag
                code_data['redeemed'] = False

            self.code_db.write(code, code_data)
            codes.append(code)

        code_type_str = "multi-use" if is_multiuse else "single-use"
        print(f"Generated {count} {code_type_str} {code_type.tag} {duration} day codes by {player.name}:\n" + "\n".join(codes))
        player.send_message(
            f"{ColorFormat.GREEN}Generated {count} {code_type_str} {code_type.display_name} codes for {duration} days. Check console.")

        if send_webhook:
            webhooks = self.webhook_db.read("webhooks") or {}
            tag_hooks = webhooks.get(code_type.tag, {})
            url = tag_hooks.get(str(duration))
            if not url:
                url = tag_hooks.get("default")
            
            if url:
                threading.Thread(target=self.send_to_discord, args=(url, code_type.tag, duration, codes)).start()
                player.send_message(f"{ColorFormat.GREEN}Sending {count} codes to Discord webhook...")
            else:
                player.send_message(f"{ColorFormat.YELLOW}No Discord webhook configured for tag {code_type.tag} at {duration} days (and no default).")

    def send_to_discord(self, url: str, tag: str, duration: int, codes: List[str]):
        """Post codes to Discord webhook and store the message ID so it can be edited later."""
        if not url:
            return

        codes_str = "\n".join(codes)

        payload = {
            "content": None,
            "embeds": [
                {
                    "title": "🎟️ New Warp Codes Generated!",
                    "description": "Here are the newly generated codes:",
                    "color": 5814783,
                    "fields": [
                        {"name": "Tag", "value": tag, "inline": True},
                        {"name": "Duration", "value": f"{duration} days", "inline": True},
                        {"name": "Codes", "value": f"```\n{codes_str}\n```"}
                    ]
                }
            ]
        }

        try:
            # Append ?wait=true so Discord returns the full message object (with an id)
            post_url = url if "?" in url else url + "?wait=true"
            req = urllib.request.Request(
                post_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "WarpCodes-Plugin/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read()
                try:
                    msg_data = json.loads(raw)
                    message_id = msg_data.get("id")
                    if message_id:
                        # Store a mapping: each code -> (webhook_url, message_id, all codes in batch)
                        batch_record = {
                            "webhook_url": url,
                            "message_id": message_id,
                            "tag": tag,
                            "duration": duration,
                            "codes": codes,          # full ordered list for rebuild
                            "redeemed_codes": []     # filled in as codes are redeemed
                        }
                        for code in codes:
                            self.webhook_messages_db.write(code, batch_record)
                        self.logger.info(f"Stored Discord message ID {message_id} for {len(codes)} code(s)")
                except Exception as parse_err:
                    self.logger.warning(f"Could not parse Discord response to get message ID: {parse_err}")
        except Exception as e:
            self.logger.error(f"Failed to send webhook to discord: {e}")

    def _edit_discord_message(self, url: str, message_id: str, tag: str, duration: int,
                              all_codes: List[str], redeemed_codes: List[str]):
        """Edit a previously posted Discord webhook message to show which codes have been redeemed."""
        if not url or not message_id:
            return

        # Parse webhook id + token from the URL
        # URL format: https://discord.com/api/webhooks/{id}/{token}
        try:
            parts = url.rstrip("/").split("/")
            # Find 'webhooks' segment
            wh_idx = None
            for i, p in enumerate(parts):
                if p == "webhooks":
                    wh_idx = i
                    break
            if wh_idx is None or wh_idx + 2 >= len(parts):
                self.logger.warning("Cannot parse webhook URL for message edit")
                return
            webhook_id = parts[wh_idx + 1]
            webhook_token = parts[wh_idx + 2]
        except Exception as e:
            self.logger.warning(f"Error parsing webhook URL: {e}")
            return

        # Rebuild the codes field with redeemed ones struck-through
        redeemed_set = set(redeemed_codes)
        lines = []
        for code in all_codes:
            if code in redeemed_set:
                lines.append(f"~~{code}~~ ✅ redeemed")
            else:
                lines.append(code)
        codes_display = "\n".join(lines)

        remaining = len(all_codes) - len(redeemed_codes)
        title = "🎟️ Warp Codes" + (" (all redeemed)" if remaining == 0 else f" ({remaining} remaining)")
        embed_color = 3158064 if remaining == 0 else 5814783  # grey when all gone, purple otherwise

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": "Codes are struck-through once redeemed:",
                    "color": embed_color,
                    "fields": [
                        {"name": "Tag", "value": tag, "inline": True},
                        {"name": "Duration", "value": f"{duration} days", "inline": True},
                        {"name": "Codes", "value": f"```\n{codes_display}\n```"}
                    ]
                }
            ]
        }

        edit_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}"
        try:
            req = urllib.request.Request(
                edit_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "WarpCodes-Plugin/1.0"},
                method="PATCH"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
            self.logger.info(f"Updated Discord message {message_id} after code redemption")
        except Exception as e:
            self.logger.error(f"Failed to edit Discord message {message_id}: {e}")

    def notify_discord_code_redeemed(self, code: str, player_name: str):
        """Called after a code is redeemed – edits the original Discord message to mark it."""
        record = self.webhook_messages_db.read(code)
        if not record:
            return  # This code was never posted to Discord, nothing to update

        # Add this code to the redeemed list (avoid duplicates)
        redeemed_list = record.get("redeemed_codes", [])
        if code not in redeemed_list:
            redeemed_list.append(code)
            record["redeemed_codes"] = redeemed_list
            # Persist the updated record for all codes in this batch
            for batch_code in record.get("codes", []):
                self.webhook_messages_db.write(batch_code, record)

        # Fire the edit on a background thread so the game thread isn't blocked
        threading.Thread(
            target=self._edit_discord_message,
            args=(
                record["webhook_url"],
                record["message_id"],
                record["tag"],
                record["duration"],
                record["codes"],
                redeemed_list
            )
        ).start()

    def get_unredeemed_codes(self) -> dict:
        """Returns a dict keyed by tag name with lists of unredeemed code keys"""
        codes_by_tag = {ct.tag: [] for ct in self.CODE_TYPES.values()}

        for key in self.code_db.keys():
            data = self.code_db.read(key)
            if data:
                code_tag = data.get('type')
                # Initialize the list if this tag exists but wasn't in CODE_TYPES yet
                if code_tag not in codes_by_tag:
                    codes_by_tag[code_tag] = []

                # For multi-use codes, they're always available (never fully "redeemed")
                # For single-use codes, check the redeemed flag
                is_multiuse = data.get('isMultiUse', False)
                is_available = is_multiuse or not data.get('redeemed', False)

                if is_available:
                    codes_by_tag[code_tag].append(key)

        return codes_by_tag

    def process_redemption(self, player, code: str):
        try:
            code_data = self.code_db.read(code)

            if not code_data:
                player.send_error_message(f"{ColorFormat.RED}Invalid code!")
                return

            # Check if this is a multi-use code
            is_multiuse = code_data.get('isMultiUse', False)
            player_uuid_str = str(player.unique_id)

            if is_multiuse:
                # For multi-use codes, check if this specific player has already redeemed it
                redeemed_by_list = code_data.get('redeemedBy', [])
                if player_uuid_str in redeemed_by_list:
                    player.send_error_message(f"{ColorFormat.RED}You have already redeemed this code!")
                    return
            else:
                # For single-use codes, use the traditional redeemed check
                if code_data.get('redeemed', False):
                    redeemed_by = code_data.get('redeemedByName', 'Unknown')
                    redeemed_time = code_data.get('redeemedAt', 0)
                    if redeemed_time:
                        redeemed_date = time.strftime('%Y-%m-%d %H:%M', time.localtime(redeemed_time / 1000))
                        player.send_error_message(f"{ColorFormat.RED}Already redeemed by {redeemed_by} on {redeemed_date}!")
                    else:
                        player.send_error_message(f"{ColorFormat.RED}Already redeemed!")
                    return

            try:
                duration = int(code_data.get('duration', 0))
                if duration <= 0:
                    raise ValueError("Invalid duration")
            except (ValueError, TypeError):
                self.logger.error(f"Invalid duration for code {code}")
                player.send_error_message(f"{ColorFormat.RED}Corrupted code!")
                return

            code_type = self.TAG_TO_TYPE.get(code_data.get('type'))
            if not code_type:
                self.logger.error(f"Invalid type for code {code}")
                player.send_error_message(f"{ColorFormat.RED}Unknown code type!")
                return

            # Mark as redeemed with appropriate tracking based on code type
            redemption_time = time.time() * 1000

            if is_multiuse:
                # For multi-use codes, add this player to the redeemed list
                if 'redeemedBy' not in code_data:
                    code_data['redeemedBy'] = []
                code_data['redeemedBy'].append(player_uuid_str)

                # Also track redemption details for multi-use codes
                if 'redemptions' not in code_data:
                    code_data['redemptions'] = []
                code_data['redemptions'].append({
                    'playerUUID': player_uuid_str,
                    'playerName': player.name,
                    'redeemedAt': redemption_time
                })
            else:
                # For single-use codes, use traditional tracking
                code_data['redeemed'] = True
                code_data['redeemedBy'] = player.unique_id
                code_data['redeemedByName'] = player.name
                code_data['redeemedAt'] = redemption_time

            self.code_db.write(code, code_data)

            # Notify Discord that this code has been redeemed (edit the posted message)
            threading.Thread(
                target=self.notify_discord_code_redeemed,
                args=(code, player.name)
            ).start()

            self.apply_code_effect(player, code_type, duration, code, code_data)

        except Exception as e:
            self.logger.error(f"Error in process_redemption for code {code}: {e}")
            player.send_error_message(f"{ColorFormat.RED}An error occurred while processing the code!")

    def apply_code_effect(self, player, code_type: CodeType, duration: int, code: str, code_data: dict):
        try:
            current_time = time.time() * 1000
            new_duration_ms = duration * 24 * 60 * 60 * 1000

            player_data = self.player_db.read(player.unique_id) or {}
            player_data['name'] = player.name
            player_data['lastSeen'] = current_time

            if 'activeCodes' not in player_data or not isinstance(player_data['activeCodes'], list):
                player_data['activeCodes'] = []

            if 'redemptionHistory' not in player_data or not isinstance(player_data['redemptionHistory'], list):
                player_data['redemptionHistory'] = []

            # Check if player already has an active code of this type
            existing_code_index = None
            latest_expiration = current_time
            stacked_expires_at = None
            expires_at = None

            for i, existing_code in enumerate(player_data['activeCodes']):
                if (existing_code.get('type') == code_type.tag and
                    existing_code.get('expiresAt', 0) > current_time):
                    # Found an active code of the same type
                    if existing_code.get('expiresAt', 0) > latest_expiration:
                        latest_expiration = existing_code.get('expiresAt', 0)
                        existing_code_index = i

            if existing_code_index is not None:
                # Stack the time: extend from the current expiration time
                stacked_expires_at = latest_expiration + new_duration_ms

                # Update the existing code entry with stacked time
                player_data['activeCodes'][existing_code_index].update({
                    'expiresAt': stacked_expires_at,
                    'lastExtendedAt': current_time,
                    'lastExtendedBy': code,
                    'totalExtensions': player_data['activeCodes'][existing_code_index].get('totalExtensions', 0) + 1,
                    'stackedDuration': player_data['activeCodes'][existing_code_index].get('stackedDuration', 0) + duration
                })

                # Remove any other codes of the same type (consolidate into one entry)
                player_data['activeCodes'] = [
                    c for i, c in enumerate(player_data['activeCodes'])
                    if not (c.get('type') == code_type.tag and i != existing_code_index and c.get('expiresAt', 0) > current_time)
                ]

                # Calculate total days for user message
                total_remaining_ms = stacked_expires_at - current_time
                total_days = int(total_remaining_ms / (24 * 60 * 60 * 1000))

                player.send_message(
                    f"{ColorFormat.GREEN}Extended {code_type.display_name} ({code_type.tag}) by {duration} days! "
                    f"Total time remaining: {total_days} days")

            else:
                # No existing active code of this type, create new entry
                expires_at = current_time + new_duration_ms
                active_code_entry = {
                    'type': code_type.tag,
                    'typeName': code_type.display_name,
                    'expiresAt': expires_at,
                    'redeemedAt': current_time,
                    'code': code,
                    'duration': duration,
                    'totalExtensions': 0,
                    'stackedDuration': 0
                }
                player_data['activeCodes'].append(active_code_entry)

                player.send_message(
                    f"{ColorFormat.GREEN}Redeemed {code_type.display_name} ({code_type.tag}) for {duration} days!")

            # Add to redemption history (always add regardless of stacking)
            final_expires_at = (stacked_expires_at if existing_code_index is not None else expires_at)
            history_entry = {
                'code': code,
                'type': code_type.tag,
                'typeName': code_type.display_name,
                'duration': duration,
                'redeemedAt': current_time,
                'expiresAt': final_expires_at,
                'generatedBy': code_data.get('generatedBy', 'Unknown'),
                'wasStacked': existing_code_index is not None
            }
            player_data['redemptionHistory'].append(history_entry)

            self.player_db.write(player.unique_id, player_data)

            # Schedule future expiration check with error handling
            try:
                final_duration_ms = final_expires_at - current_time
                ticks_delay = int((final_duration_ms / 1000) * 20)
                self.server.scheduler.run_task(self, lambda: self.safe_check_and_update_player(player), delay=ticks_delay)
            except Exception as e:
                self.logger.warning(f"Could not schedule expiration check: {e}")

            # Immediate tag update with error handling
            self.safe_check_and_update_player(player)

        except Exception as e:
            self.logger.error(f"Error in apply_code_effect: {e}")
            # Don't send error message here since success message may have already been sent

    def transfer_build_tag(self, sender, target_name: str):
        target_player = None
        target_name = target_name.strip()

        online_names = [p.name for p in self.server.online_players]

        for p in self.server.online_players:
            if p.name.lower() == target_name.lower():
                target_player = p
                break

        if not target_player:
            sender.send_error_message(f'{ColorFormat.RED}Player "{target_name}" not found!')
            sender.send_message(f'{ColorFormat.YELLOW}Online players: {", ".join(online_names)}')
            return

        if target_player.unique_id == sender.unique_id:
            sender.send_error_message(f"{ColorFormat.RED}Cannot transfer to yourself!")
            return

        sender_data = self.player_db.read(sender.unique_id) or {}
        if 'activeCodes' not in sender_data:
            sender_data['activeCodes'] = []

        now = time.time() * 1000
        build_codes = [c for c in sender_data['activeCodes']
                       if c.get('type') == self.CODE_TYPES['BUILD'].tag and c.get('expiresAt', 0) > now]

        if not build_codes:
            sender.send_error_message(f"{ColorFormat.RED}No build code to transfer!")
            return

        code_to_transfer = max(build_codes, key=lambda c: c.get('expiresAt', 0))
        remaining_time = code_to_transfer['expiresAt'] - now

        sender_data['activeCodes'] = [c for c in sender_data['activeCodes'] if c != code_to_transfer]
        self.player_db.write(sender.unique_id, sender_data)

        target_data = self.player_db.read(target_player.unique_id) or {}
        target_data['name'] = target_player.name
        if 'activeCodes' not in target_data:
            target_data['activeCodes'] = []

        target_data['activeCodes'].append({'type': self.CODE_TYPES['BUILD'].tag, 'expiresAt': now + remaining_time})
        self.player_db.write(target_player.unique_id, target_data)

        self.check_and_update_player(sender)
        self.check_and_update_player(target_player)

        days = int(remaining_time / (1000 * 60 * 60 * 24))
        sender.send_message(f"{ColorFormat.GREEN}Transferred build tag to {target_player.name}.")
        target_player.send_message(f"{ColorFormat.GREEN}Received build tag from {sender.name} for {days} days!")

    def check_and_show_player_time(self, player):
        """Update player codes and then show their time"""
        # First, check and update the player's codes
        self.safe_check_and_update_player(player)
        # Then show their current time
        self.check_player_time(player)

    def check_player_time(self, player):
        player_data = self.player_db.read(player.unique_id)
        now = time.time() * 1000

        active_codes = []
        if player_data and 'activeCodes' in player_data:
            active_codes = [c for c in player_data['activeCodes'] if c.get('expiresAt', 0) > now]

        if not active_codes:
            player.send_message(f"{ColorFormat.YELLOW}No active codes.")
            return

        player.send_message(f"{ColorFormat.GREEN}--- Your Active Codes ---")

        # Group codes by type and show stacking info
        code_groups = {}
        for code in active_codes:
            tag = code.get('type')
            if tag not in code_groups:
                code_groups[tag] = []
            code_groups[tag].append(code)

        for tag, codes in code_groups.items():
            # Find the code with the latest expiration (should be only one after stacking)
            latest_code = max(codes, key=lambda c: c.get('expiresAt', 0))
            expires_at = latest_code.get('expiresAt', 0)
            remaining_ms = expires_at - now
            formatted_time = self.format_remaining_time(remaining_ms)

            # Show stacking information if available
            total_extensions = latest_code.get('totalExtensions', 0)
            stacked_duration = latest_code.get('stackedDuration', 0)

            if total_extensions > 0:
                player.send_message(
                    f"{ColorFormat.YELLOW}- Tag [{tag}]: {ColorFormat.WHITE}{formatted_time} "
                    f"{ColorFormat.GRAY}(Extended {total_extensions} times, +{stacked_duration} days stacked)"
                )
            else:
                player.send_message(f"{ColorFormat.YELLOW}- Tag [{tag}]: {ColorFormat.WHITE}{formatted_time}")

    def view_all_player_times(self, admin_player):
        now = time.time() * 1000
        online_players = {p.unique_id: p.name for p in self.server.online_players}
        found = False

        admin_player.send_message(f"{ColorFormat.GREEN}--- All Active Player Codes ---")

        for player_id in self.player_db.keys():
            player_data = self.player_db.read(player_id)
            if not player_data or 'activeCodes' not in player_data:
                continue

            active_codes = [c for c in player_data['activeCodes'] if c.get('expiresAt', 0) > now]

            if active_codes:
                found = True
                player_name = player_data.get('name') or online_players.get(player_id, f"Player ({player_id[:8]}...)")
                admin_player.send_message(f"\n{ColorFormat.YELLOW}Player: {ColorFormat.WHITE}{player_name}")

                latest_expirations = {}
                for code in active_codes:
                    tag = code.get('type')
                    expires_at = code.get('expiresAt', 0)
                    if tag not in latest_expirations or expires_at > latest_expirations[tag]:
                        latest_expirations[tag] = expires_at

                for tag, expires_at in latest_expirations.items():
                    remaining_ms = expires_at - now
                    formatted_time = self.format_remaining_time(remaining_ms)
                    admin_player.send_message(f"{ColorFormat.GRAY}- Tag [{tag}]: {ColorFormat.WHITE}{formatted_time}")

        if not found:
            admin_player.send_message(f"{ColorFormat.YELLOW}No active codes found.")

    def update_all_player_names(self, admin_player):
        admin_player.send_message(f"{ColorFormat.YELLOW}Updating player names...")

        online_players = {p.unique_id: p.name for p in self.server.online_players}
        updated = 0
        offline = 0

        for player_id in self.player_db.keys():
            player_data = self.player_db.read(player_id)
            if not player_data or player_data.get('name'):
                continue

            if player_id in online_players:
                player_data['name'] = online_players[player_id]
                self.player_db.write(player_id, player_data)
                updated += 1
            else:
                offline += 1

        admin_player.send_message(f"{ColorFormat.GREEN}Complete! Updated: {updated}, Offline: {offline}")

    def clear_unredeemed_codes(self, player):
        deleted = 0
        for code_key in list(self.code_db.keys()):
            code_data = self.code_db.read(code_key)
            if code_data:
                # Delete unredeemed single-use codes and all multi-use codes
                is_multiuse = code_data.get('isMultiUse', False)
                should_delete = is_multiuse or not code_data.get('redeemed', False)

                if should_delete:
                    self.code_db.delete(code_key)
                    deleted += 1

        player.send_message(f"{ColorFormat.GREEN}Deleted {deleted} codes (including multi-use codes).")

    def show_statistics(self, player):
        """Show comprehensive statistics about codes and redemptions"""
        total_codes = len(self.code_db.keys())
        single_use_redeemed = 0
        single_use_unredeemed = 0
        multi_use_codes = 0
        total_multi_use_redemptions = 0
        codes_by_type = {}
        redeemed_by_type = {}

        for code_key in self.code_db.keys():
            code_data = self.code_db.read(code_key)
            if code_data:
                code_type = code_data.get('type', 'Unknown')
                codes_by_type[code_type] = codes_by_type.get(code_type, 0) + 1

                is_multiuse = code_data.get('isMultiUse', False)

                if is_multiuse:
                    multi_use_codes += 1
                    redemption_count = len(code_data.get('redeemedBy', []))
                    total_multi_use_redemptions += redemption_count
                    redeemed_by_type[code_type] = redeemed_by_type.get(code_type, 0) + redemption_count
                else:
                    if code_data.get('redeemed', False):
                        single_use_redeemed += 1
                        redeemed_by_type[code_type] = redeemed_by_type.get(code_type, 0) + 1
                    else:
                        single_use_unredeemed += 1

        # Player statistics
        total_players = len(self.player_db.keys())
        active_players = 0
        total_redemptions = 0

        for player_id in self.player_db.keys():
            player_data = self.player_db.read(player_id)
            if player_data:
                history = player_data.get('redemptionHistory', [])
                if history:
                    active_players += 1
                    total_redemptions += len(history)

        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")
        player.send_message(f"{ColorFormat.GREEN}System Statistics")
        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")
        player.send_message(f"{ColorFormat.YELLOW}Total Codes Generated: {ColorFormat.WHITE}{total_codes}")
        player.send_message(f"{ColorFormat.YELLOW}Single-use Codes: {ColorFormat.WHITE}{total_codes - multi_use_codes}")
        player.send_message(f"  {ColorFormat.GREEN}Redeemed: {single_use_redeemed}")
        player.send_message(f"  {ColorFormat.WHITE}Unredeemed: {single_use_unredeemed}")
        player.send_message(f"{ColorFormat.YELLOW}Multi-use Codes: {ColorFormat.WHITE}{multi_use_codes}")
        player.send_message(f"  {ColorFormat.GREEN}Total Redemptions: {total_multi_use_redemptions}")
        total_redemptions = single_use_redeemed + total_multi_use_redemptions
        player.send_message(f"{ColorFormat.YELLOW}Total Redemptions: {ColorFormat.GREEN}{total_redemptions}")
        player.send_message("")
        player.send_message(f"{ColorFormat.AQUA}Codes by Type:")
        for code_type, count in codes_by_type.items():
            redeemed = redeemed_by_type.get(code_type, 0)
            player.send_message(
                f"  {ColorFormat.YELLOW}{code_type}: {ColorFormat.WHITE}{count} total, {ColorFormat.GREEN}{redeemed} redeemed")
        player.send_message("")
        player.send_message(f"{ColorFormat.AQUA}Player Statistics:")
        player.send_message(f"{ColorFormat.YELLOW}Total Players: {ColorFormat.WHITE}{total_players}")
        player.send_message(f"{ColorFormat.YELLOW}Active Players: {ColorFormat.GREEN}{active_players}")
        player.send_message(f"{ColorFormat.YELLOW}Total Redemptions: {ColorFormat.WHITE}{total_redemptions}")
        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")

    # --- Manage Code Types UI ---

    def show_manage_code_types_menu(self, player):
        """Show the code type management menu"""
        form = ActionForm()
        form.title = "Manage Code Types"

        # Count custom types
        custom_count = len(self.CODE_TYPES) - len(self.BUILTIN_CODE_TYPES)
        form.content = (
            f"Built-in types: {len(self.BUILTIN_CODE_TYPES)}\n"
            f"Custom types: {custom_count}\n\n"
            f"Current types:\n"
        )
        for key, ct in self.CODE_TYPES.items():
            is_builtin = key in self.BUILTIN_CODE_TYPES
            label = "built-in" if is_builtin else "custom"
            form.content += f"  - {ct.display_name} (tag: {ct.tag}) [{label}]\n"

        def on_submit(p, button_index):
            if button_index == 0:
                self.show_add_code_type_form(p)
            elif button_index == 1:
                self.show_delete_code_type_menu(p)
            elif button_index == 2:
                self.show_main_menu(p)

        form.on_submit = on_submit
        form.add_button("Add New Code Type")
        form.add_button("Delete Custom Code Type")
        form.add_button("Back")

        player.send_form(form)

    def show_add_code_type_form(self, player):
        """Show form to add a new custom code type"""
        tag_input = TextInput(
            label="tag",
            placeholder="Tag name (e.g. space, vip, premium)",
            default_value=""
        )
        display_input = TextInput(
            label="display",
            placeholder="Display name (e.g. Space, VIP, Premium)",
            default_value=""
        )

        def on_submit(p, json_data):
            try:
                data = json.loads(json_data) if isinstance(json_data, str) else json_data

                if isinstance(data, list):
                    if len(data) < 2:
                        p.send_error_message(f"{ColorFormat.RED}Invalid form data!")
                        return
                    tag = str(data[0]).strip()
                    display_name = str(data[1]).strip()
                else:
                    tag = data.get("tag", "").strip()
                    display_name = data.get("display", "").strip()

                if not tag:
                    p.send_error_message(f"{ColorFormat.RED}Tag name cannot be empty!")
                    return

                if not display_name:
                    p.send_error_message(f"{ColorFormat.RED}Display name cannot be empty!")
                    return

                # Check for duplicate tags
                for existing_ct in self.CODE_TYPES.values():
                    if existing_ct.tag.lower() == tag.lower():
                        p.send_error_message(f"{ColorFormat.RED}A code type with tag '{tag}' already exists!")
                        return

                # Create a safe internal key from the display name
                internal_key = display_name.upper().replace(' ', '_')

                # Check for duplicate keys
                if internal_key in self.CODE_TYPES:
                    p.send_error_message(f"{ColorFormat.RED}A code type with key '{internal_key}' already exists!")
                    return

                # Save to database
                self.type_db.write(internal_key, {
                    'tag': tag,
                    'display_name': display_name
                })

                # Add empty webhook config structure for the new tag
                webhooks = self.webhook_db.read("webhooks") or {}
                if tag not in webhooks:
                    webhooks[tag] = {"default": ""}
                    self.webhook_db.write("webhooks", webhooks)

                # Reload all code types
                self.reload_code_types()

                p.send_message(
                    f"{ColorFormat.GREEN}Added new code type: {display_name} (tag: {tag})\n"
                    f"{ColorFormat.YELLOW}You can now generate codes with this type from the Generate menu!"
                )

                # Show the manage menu again
                self.server.scheduler.run_task(self, lambda: self.show_manage_code_types_menu(p), delay=10)

            except (ValueError, json.JSONDecodeError):
                p.send_error_message(f"{ColorFormat.RED}Invalid input!")

        form = ModalForm(
            title="Add New Code Type",
            controls=[tag_input, display_input],
            on_submit=on_submit
        )
        player.send_form(form)

    def show_delete_code_type_menu(self, player):
        """Show menu to delete a custom code type"""
        # Get only custom types (not built-in)
        custom_keys = [key for key in self.CODE_TYPES if key not in self.BUILTIN_CODE_TYPES]

        if not custom_keys:
            player.send_error_message(f"{ColorFormat.YELLOW}No custom code types to delete.")
            return

        form = ActionForm()
        form.title = "Delete Custom Code Type"
        form.content = (
            f"{ColorFormat.RED}WARNING: Deleting a code type will not remove existing codes "
            f"of that type, but you won't be able to generate new ones.\n\n"
            f"Select a custom type to delete:"
        )

        def on_submit(p, button_index):
            if button_index == len(custom_keys):
                # Back button
                self.show_manage_code_types_menu(p)
                return

            if 0 <= button_index < len(custom_keys):
                key_to_delete = custom_keys[button_index]
                ct = self.CODE_TYPES.get(key_to_delete)
                if ct:
                    self.show_confirm_delete_code_type(p, key_to_delete, ct)

        form.on_submit = on_submit
        for key in custom_keys:
            ct = self.CODE_TYPES[key]
            form.add_button(f"{ct.display_name} (tag: {ct.tag})")
        form.add_button("Back")

        player.send_form(form)

    def show_confirm_delete_code_type(self, player, key: str, code_type: CodeType):
        """Confirm deletion of a custom code type"""
        form = ActionForm()
        form.title = f"{ColorFormat.RED}Confirm Delete"
        form.content = (
            f"Are you sure you want to delete the code type:\n\n"
            f"  Name: {code_type.display_name}\n"
            f"  Tag: {code_type.tag}\n\n"
            f"{ColorFormat.RED}This cannot be undone!"
        )

        def on_submit(p, button_index):
            if button_index == 0:
                # Delete it
                self.type_db.delete(key)
                self.reload_code_types()
                p.send_message(f"{ColorFormat.GREEN}Deleted code type: {code_type.display_name} (tag: {code_type.tag})")
                self.server.scheduler.run_task(self, lambda: self.show_manage_code_types_menu(p), delay=10)
            else:
                p.send_message(f"{ColorFormat.YELLOW}Cancelled.")
                self.show_manage_code_types_menu(p)

        form.on_submit = on_submit
        form.add_button(f"{ColorFormat.GREEN}Yes, Delete")
        form.add_button(f"{ColorFormat.RED}Cancel")

        player.send_form(form)

    def show_admin_help(self, player):
        """Show admin help and troubleshooting information"""
        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")
        player.send_message(f"{ColorFormat.GREEN}Warp Code Manager - Admin Help")
        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")
        player.send_message(f"{ColorFormat.YELLOW}Available Commands:")
        player.send_message(f"{ColorFormat.WHITE}/codes - Open code management menu (requires op)")
        player.send_message(f"{ColorFormat.WHITE}/warp - Alias for /codes")
        player.send_message(f"{ColorFormat.WHITE}/warpcodes - Alias for /codes")
        player.send_message(f"{ColorFormat.WHITE}/viewall - View all players' active codes (requires op)")
        player.send_message(f"{ColorFormat.WHITE}/stats - Show system statistics (requires op)")
        player.send_message(f"{ColorFormat.WHITE}/clearunredeemed - Clear unredeemed codes (requires op)")
        player.send_message(f"{ColorFormat.WHITE}/redeem - Open code redemption form")
        player.send_message("")
        player.send_message(f"{ColorFormat.YELLOW}Chat Commands (work for ops or Admin tag):")
        player.send_message(f"{ColorFormat.WHITE}-generate - Generate codes (fallback if /codes not working)")
        player.send_message(f"{ColorFormat.WHITE}-stats - Show system statistics")
        player.send_message(f"{ColorFormat.WHITE}-viewall - View all player codes")
        player.send_message(f"{ColorFormat.WHITE}-clearunredeemedcodes - Delete unredeemed codes")
        player.send_message(f"{ColorFormat.WHITE}-help - Show this help")
        player.send_message("")
        player.send_message(f"{ColorFormat.YELLOW}Troubleshooting /codes command:")
        player.send_message(f"{ColorFormat.WHITE}1. Make sure you have operator status")
        player.send_message(f"{ColorFormat.WHITE}2. Try aliases: /warp or /warpcodes")
        player.send_message(f"{ColorFormat.WHITE}3. Check server console for errors")
        player.send_message(f"{ColorFormat.WHITE}4. Verify plugin loaded successfully")
        player.send_message(f"{ColorFormat.GOLD}{'=' * 50}")

    def fix_all_player_tags(self, admin_player):
        """Fix tags for all players by checking their active codes and reactivating valid tags"""
        admin_player.send_message(f"{ColorFormat.YELLOW}Fixing tags for all players...")

        now = time.time() * 1000
        fixed_count = 0
        total_players = 0

        # Check all players in database
        for player_id in self.player_db.keys():
            player_data = self.player_db.read(player_id)
            if not player_data:
                continue

            total_players += 1
            player_name = player_data.get('name', f'Player-{str(player_id)[:8]}')

            # Find valid codes
            valid_codes = []
            if 'activeCodes' in player_data:
                valid_codes = [c for c in player_data['activeCodes'] if c.get('expiresAt', 0) > now]

            if valid_codes:
                # Find the player if they're online
                online_player = None
                for p in self.server.online_players:
                    if str(p.unique_id) == str(player_id):
                        online_player = p
                        break

                if online_player:
                    # Check and fix their tags
                    active_tags = {c.get('type') for c in valid_codes}
                    tags_fixed = False

                    for code_type in self.CODE_TYPES.values():
                        should_have = code_type.tag in active_tags
                        has_tag = self.player_has_tag(online_player, code_type.tag)

                        if should_have and not has_tag:
                            self.add_player_tag(online_player, code_type.tag)
                            tags_fixed = True
                        elif not should_have and has_tag:
                            self.remove_player_tag(online_player, code_type.tag)
                            tags_fixed = True

                    if tags_fixed:
                        fixed_count += 1
                        admin_player.send_message(f"{ColorFormat.GREEN}Fixed tags for {player_name}")

        admin_player.send_message(f"{ColorFormat.GREEN}Tag fix complete!")
        admin_player.send_message(f"{ColorFormat.YELLOW}Checked {total_players} players, fixed {fixed_count} players")

    def show_player_history(self, player):
        """Show player's own redemption history"""
        player_data = self.player_db.read(player.unique_id)

        if not player_data or not player_data.get('redemptionHistory'):
            player.send_message(f"{ColorFormat.YELLOW}You have no redemption history.")
            return

        history = player_data.get('redemptionHistory', [])

        player.send_message(f"{ColorFormat.GREEN}{'=' * 50}")
        player.send_message(f"{ColorFormat.GOLD}Your Redemption History")
        player.send_message(f"{ColorFormat.GREEN}{'=' * 50}")

        for idx, entry in enumerate(reversed(history[-20:]), 1):  # Show last 20
            code = entry.get('code', 'Unknown')
            type_name = entry.get('typeName', entry.get('type', 'Unknown'))
            duration = entry.get('duration', 0)
            redeemed_at = entry.get('redeemedAt', 0)
            expires_at = entry.get('expiresAt', 0)
            generated_by = entry.get('generatedBy', 'Unknown')

            redeemed_date = time.strftime('%Y-%m-%d %H:%M',
                                          time.localtime(redeemed_at / 1000)) if redeemed_at else 'Unknown'

            now = time.time() * 1000
            if expires_at > now:
                status = f"{ColorFormat.GREEN}Active"
                remaining = self.format_remaining_time(expires_at - now)
                time_info = f"({remaining} left)"
            else:
                status = f"{ColorFormat.GRAY}Expired"
                time_info = ""

            player.send_message(f"\n{ColorFormat.YELLOW}{idx}. {ColorFormat.WHITE}Code: {code}")
            player.send_message(f"   Type: {type_name} | Duration: {duration} days")
            player.send_message(f"   Redeemed: {redeemed_date}")
            player.send_message(f"   Status: {status} {time_info}")
            player.send_message(f"   Generated by: {generated_by}")

        if len(history) > 20:
            player.send_message(f"\n{ColorFormat.GRAY}Showing last 20 of {len(history)} total redemptions")

        player.send_message(f"{ColorFormat.GREEN}{'=' * 50}")

    def safe_check_and_update_player(self, player):
        """Safe wrapper for check_and_update_player with exception handling"""
        try:
            self.check_and_update_player(player)
        except Exception as e:
            player_name = getattr(player, 'name', 'Unknown') if player else 'None'
            self.logger.warning(f"Error updating player {player_name}: {e}")
            import traceback
            self.logger.debug(f"Full traceback: {traceback.format_exc()}")

    def check_and_update_player(self, player):
        if not player:
            return

        # Check if player is still online by checking if they're in the online players list
        try:
            online_players = [p for p in self.server.online_players]
            if player not in online_players:
                return
        except Exception:
            # If we can't check online status, continue anyway
            pass

        # Ensure player has required attributes
        if not hasattr(player, 'unique_id') or not hasattr(player, 'name'):
            return

        now = time.time() * 1000
        player_data = self.player_db.read(player.unique_id)

        if not player_data:
            return

        valid_codes = []
        expired_codes = []
        if 'activeCodes' in player_data:
            for code in player_data['activeCodes']:
                expires_at = code.get('expiresAt', 0)
                if expires_at > now:
                    valid_codes.append(code)
                else:
                    expired_codes.append(code)

        if expired_codes:
            # Remove expired codes from database
            player_data['activeCodes'] = valid_codes
            self.player_db.write(player.unique_id, player_data)

        active_tags = {c.get('type') for c in valid_codes}

        for code_type in self.CODE_TYPES.values():
            should_have = code_type.tag in active_tags
            has_tag = self.player_has_tag(player, code_type.tag)

            if should_have and not has_tag:
                self.add_player_tag(player, code_type.tag)
            elif not should_have and has_tag:
                # Double-check before removing - make sure there really are no valid codes
                double_check_codes = [c for c in valid_codes if c.get('type') == code_type.tag]
                if not double_check_codes:
                    self.remove_player_tag(player, code_type.tag)
                try:
                    player.send_message(f"{ColorFormat.YELLOW}Your {code_type.tag} expired.")
                except Exception as e:
                    self.logger.warning(f"Could not send expiration message to {player.name}: {e}")

        all_codes = player_data.get('activeCodes', []) if player_data else []
        needs_cleanup = len(valid_codes) < len(all_codes)
        needs_name = not player_data or player_data.get('name') != player.name

        if needs_cleanup or needs_name:
            latest_data = self.player_db.read(player.unique_id) or {'activeCodes': []}
            data_to_write = dict(latest_data)

            if needs_cleanup:
                data_to_write['activeCodes'] = [c for c in latest_data.get('activeCodes', [])
                                                if c.get('expiresAt', 0) > now]
            if needs_name:
                data_to_write['name'] = player.name

            self.player_db.write(player.unique_id, data_to_write)

    def check_all_players(self):
        for player in self.server.online_players:
            self.safe_check_and_update_player(player)

    def format_remaining_time(self, milliseconds: float) -> str:
        if milliseconds <= 0:
            return "Expired"

        total_seconds = int(milliseconds / 1000)
        days = total_seconds // 86400
        total_seconds %= 86400
        hours = total_seconds // 3600
        total_seconds %= 3600
        minutes = total_seconds // 60

        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days > 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")

        return ', '.join(parts) if parts else "< 1 minute"

    def player_has_tag(self, player, tag: str) -> bool:
        """Check if a player has a specific native Minecraft scoreboard tag"""
        try:
            return tag in player.scoreboard_tags
        except Exception:
            return False

    def add_player_tag(self, player, tag: str):
        """Add a native Minecraft scoreboard tag to a player using server command"""
        try:
            # Check if player already has the tag
            if tag in player.scoreboard_tags:
                return

            # Execute the tag command as the server/console
            command = f"tag \"{player.name}\" add {tag}"
            self.server.dispatch_command(self.server.command_sender, command)

        except Exception as e:
            self.logger.error(f"Error adding native tag {tag} to {player.name}: {e}")

    def remove_player_tag(self, player, tag: str):
        """Remove a native Minecraft scoreboard tag from a player using server command"""
        try:
            # Check if player has the tag
            if tag not in player.scoreboard_tags:
                return

            # Execute the tag command as the server/console
            command = f"tag \"{player.name}\" remove {tag}"
            self.server.dispatch_command(self.server.command_sender, command)

        except Exception as e:
            self.logger.error(f"Error removing native tag {tag} from {player.name}: {e}")