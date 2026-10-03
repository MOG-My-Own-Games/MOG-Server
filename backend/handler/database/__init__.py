from .games_handler import DBGamesHandler
from .install_sessions_handler import DBInstallSessionsHandler
from .libraries_handler import DBLibrariesHandler
from .notifications_handler import DBNotificationsHandler
from .settings_handler import DBSettingsHandler
from .users_handler import DBUsersHandler

db_game_handler = DBGamesHandler()
db_install_session_handler = DBInstallSessionsHandler()
db_library_handler = DBLibrariesHandler()
db_notification_handler = DBNotificationsHandler()
db_settings_handler = DBSettingsHandler()
db_user_handler = DBUsersHandler()
