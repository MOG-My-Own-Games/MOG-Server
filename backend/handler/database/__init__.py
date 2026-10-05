from .devices_handler import DBDevicesHandler
from .games_handler import DBGamesHandler
from .install_sessions_handler import DBInstallSessionsHandler
from .libraries_handler import DBLibrariesHandler
from .notifications_handler import DBNotificationsHandler
from .saves_handler import DBSavesHandler
from .settings_handler import DBSettingsHandler
from .users_handler import DBUsersHandler

db_device_handler = DBDevicesHandler()
db_game_handler = DBGamesHandler()
db_install_session_handler = DBInstallSessionsHandler()
db_library_handler = DBLibrariesHandler()
db_notification_handler = DBNotificationsHandler()
db_saves_handler = DBSavesHandler()
db_settings_handler = DBSettingsHandler()
db_user_handler = DBUsersHandler()
