import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
# Channel IDs
CHECKIN_CHANNEL_ID = int(os.getenv("CHECKIN_CHANNEL_ID", "0")) or None
REGISTER_CHANNEL_ID = int(os.getenv("REGISTER_CHANNEL_ID", "0")) or None
DIVIDE_LOBBY_CHANNEL_ID = int(os.getenv("DIVIDE_LOBBY_CHANNEL_ID", "0")) or None
# Channel where result-entry embeds are posted (admin enters match scores here)
RESULT_CHANNEL_ID = int(os.getenv("RESULT_CHANNEL_ID", "0")) or None
# Role IDs
SHOWMATCH_ROLE_ID = int(os.getenv("SHOWMATCH_ROLE_ID", "0")) or None  # Role cần tag thay vì tag @everyone
# Role allowed to view lobby channels alongside admins (e.g. referee / judge role)
JUDGE_ROLE_ID = int(os.getenv("JUDGE_ROLE_ID", "0")) or None
# Category under which per-lobby voice/text channels are created
LOBBY_CATEGORY_ID = int(os.getenv("LOBBY_CATEGORY_ID", "0")) or None

