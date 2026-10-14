from .devices_handler import DBDevicesHandler
from .game_addons_handler import DBGameAddonsHandler
from .games_handler import DBGamesHandler
from .install_sessions_handler import DBInstallSessionsHandler
from .install_steps_handler import DBInstallStepsHandler
from .launch_settings_handler import DBLaunchSettingsHandler
from .libraries_handler import DBLibrariesHandler
from .notifications_handler import DBNotificationsHandler
from .saves_handler import DBSavesHandler
from .settings_handler import DBSettingsHandler
from .users_handler import DBUsersHandler

db_device_handler = DBDevicesHandler()
db_game_addons_handler = DBGameAddonsHandler()
db_game_handler = DBGamesHandler()
db_install_session_handler = DBInstallSessionsHandler()
db_install_steps_handler = DBInstallStepsHandler()
db_launch_settings_handler = DBLaunchSettingsHandler()
db_library_handler = DBLibrariesHandler()
db_notification_handler = DBNotificationsHandler()
db_saves_handler = DBSavesHandler()
db_settings_handler = DBSettingsHandler()
db_user_handler = DBUsersHandler()
