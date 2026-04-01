import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = 1143455526219432018
CHECKIN_CHANNEL_ID = 1486052246209953812
REGISTER_CHANNEL_ID = 1486416388510978251
DIVIDE_LOBBY_CHANNEL_ID = 1486418088760180917
SHOWMATCH_ROLE_ID = 1481915254140305481  # Role cần tag thay vì tag @everyone

# Channel where result-entry embeds are posted (admin enters match scores here)
RESULT_CHANNEL_ID = int(os.getenv("RESULT_CHANNEL_ID", "0")) or None
# Role allowed to view lobby channels alongside admins (e.g. referee / judge role)
JUDGE_ROLE_ID = int(os.getenv("JUDGE_ROLE_ID", "0")) or None
# Category under which per-lobby voice/text channels are created
LOBBY_CATEGORY_ID = int(os.getenv("LOBBY_CATEGORY_ID", "0")) or None

