CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS instances (
    guild_id BIGINT PRIMARY KEY,
    instance_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    token TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'online',
    activity JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    guild_id BIGINT PRIMARY KEY,
    prefixes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    google_safe_search BOOLEAN NOT NULL DEFAULT TRUE,
    reassign_roles BOOLEAN NOT NULL DEFAULT FALSE,
    welcome_removal BOOLEAN NOT NULL DEFAULT FALSE,
    system_boost_removal BOOLEAN NOT NULL DEFAULT FALSE,
    booster_role_base_id BIGINT,
    mute_role_id BIGINT,
    jail_role_id BIGINT,
    jail_channel_id BIGINT,
    reassign_ignored_roles BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    publisher_channels BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    monitored_threads BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    booster_role_include BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    lockdown_ignore BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    lockdown_role_id BIGINT
);

CREATE SCHEMA IF NOT EXISTS reposter;

CREATE TABLE IF NOT EXISTS reposter.config (
    guild_id BIGINT PRIMARY KEY,
    status BOOLEAN NOT NULL DEFAULT TRUE,
    prefix BOOLEAN NOT NULL DEFAULT FALSE,
    deletion BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS reposter.disabled (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    platform TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id, platform)
);

CREATE TABLE IF NOT EXISTS reposter.log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE SCHEMA IF NOT EXISTS commands;

CREATE TABLE IF NOT EXISTS commands.usage (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS commands.disabled (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id, command)
);

CREATE TABLE IF NOT EXISTS commands.restricted (
    guild_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    PRIMARY KEY (guild_id, role_id, command)
);

CREATE TABLE IF NOT EXISTS commands.alias (
    guild_id BIGINT NOT NULL,
    alias TEXT NOT NULL,
    invoke TEXT NOT NULL,
    command TEXT NOT NULL,
    PRIMARY KEY (guild_id, alias)
);


CREATE SCHEMA IF NOT EXISTS moderation;

CREATE TABLE IF NOT EXISTS moderation.case (
    id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'user',
    moderator_id BIGINT NOT NULL,
    message_id BIGINT,
    reason TEXT NOT NULL DEFAULT 'No reason provided',
    "action" TEXT NOT NULL DEFAULT 'unknown',
    action_expiration TIMESTAMP WITH TIME ZONE,
    action_processed BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id, guild_id)
);

CREATE TABLE IF NOT EXISTS antinuke (
    guild_id BIGINT PRIMARY KEY,
    managers BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    whitelist BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    bot_add BOOLEAN NOT NULL DEFAULT FALSE,
    "ban" JSONB,
    "kick" JSONB,
    "role" JSONB,
    "channel" JSONB,
    "webhook" JSONB,
    "emoji" JSONB
);

CREATE SCHEMA IF NOT EXISTS tickets;

CREATE TABLE IF NOT EXISTS tickets.settings (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    max_tickets INTEGER,
    inactivity_timeout INTEGER,
    staff_role_ids BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    blacklisted_ids BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    transcript_destination TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
);

CREATE TABLE IF NOT EXISTS tickets.button (
    id TEXT NOT NULL,
    guild_id BIGINT NOT NULL,
    category_id BIGINT,
    template TEXT,
    topic TEXT,
    PRIMARY KEY (id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES tickets.settings(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tickets.dropdown (
    id TEXT NOT NULL,
    guild_id BIGINT NOT NULL,
    options JSONB NOT NULL DEFAULT '[]'::JSONB,
    PRIMARY KEY (id, guild_id),
    FOREIGN KEY (guild_id) REFERENCES tickets.settings(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tickets.open (
    id TEXT NOT NULL,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (id, guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS tickets.transcript (
    id TEXT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    messages JSONB NOT NULL DEFAULT '[]'::JSONB
);

CREATE TABLE IF NOT EXISTS vanity (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    role_ids BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    template TEXT
);

CREATE TABLE IF NOT EXISTS webhook (
  identifier TEXT NOT NULL,
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  author_id BIGINT NOT NULL,
  webhook_id BIGINT NOT NULL,
  PRIMARY KEY (channel_id, webhook_id)
);

CREATE SCHEMA IF NOT EXISTS roles;

CREATE TABLE IF NOT EXISTS roles.automatic (
    guild_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,
    action TEXT NOT NULL DEFAULT 'add',
    delay INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, role_id, action)
);

CREATE TABLE IF NOT EXISTS roles.reaction (
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  role_id BIGINT NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY (guild_id, message_id, emoji)
);

CREATE TABLE IF NOT EXISTS roles.booster (
  guild_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role_id BIGINT NOT NULL,
  PRIMARY KEY (guild_id, user_id)
);


CREATE SCHEMA IF NOT EXISTS triggers;

CREATE TABLE IF NOT EXISTS triggers.reaction (
  guild_id BIGINT NOT NULL,
  trigger CITEXT NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY (guild_id, trigger, emoji)
);

CREATE TABLE IF NOT EXISTS triggers.previous_reaction (
  guild_id BIGINT NOT NULL,
  trigger CITEXT NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY (guild_id, trigger, emoji)
);

CREATE TABLE IF NOT EXISTS triggers.response (
  guild_id BIGINT NOT NULL,
  trigger CITEXT NOT NULL,
  template TEXT NOT NULL,
  strict BOOLEAN NOT NULL DEFAULT FALSE,
  reply BOOLEAN NOT NULL DEFAULT FALSE,
  delete BOOLEAN NOT NULL DEFAULT FALSE,
  paginate BOOLEAN NOT NULL DEFAULT FALSE,
  delete_after INTEGER NOT NULL DEFAULT 0,
  role_id BIGINT,
  PRIMARY KEY (guild_id, trigger)
);

CREATE TABLE IF NOT EXISTS giveaways (
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  creator_id BIGINT NOT NULL,
  prize TEXT NOT NULL,
  emoji TEXT NOT NULL,
  winners INTEGER NOT NULL,
  ended BOOLEAN NOT NULL DEFAULT FALSE,
  ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  PRIMARY KEY (guild_id, channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS highlights (
  guild_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  keyword TEXT NOT NULl,
  PRIMARY KEY (guild_id, user_id, keyword)
);

CREATE TABLE IF NOT EXISTS afk (
  user_id BIGINT PRIMARY KEY,
  reason TEXT NOT NULL DEFAULT 'AFK',
  left_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE SCHEMA IF NOT EXISTS birthday;
CREATE TABLE IF NOT EXISTS birthday.config (
    guild_id BIGINT PRIMARY KEY,
    role_id BIGINT,
    channel_id BIGINT,
    template TEXT
);

CREATE TABLE IF NOT EXISTS birthday.user (
    user_id BIGINT PRIMARY KEY,
    birthday TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS timezone (
    user_id BIGINT PRIMARY KEY,
    timezone_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth (
    user_id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    token TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS whitelist (
    guild_id BIGINT PRIMARY KEY,
    status BOOLEAN NOT NULL DEFAULT TRUE,
    action TEXT NOT NULL DEFAULT 'kick',
    "limit" INTEGER NOT NULL DEFAULT 2
);

CREATE SCHEMA IF NOT EXISTS fortnite;

CREATE TABLE IF NOT EXISTS fortnite.authorization (
    user_id BIGINT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    secret TEXT NOT NULL,
    access_token TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS fortnite.reminder (
    user_id BIGINT NOT NULL,
    cosmetic_id TEXT NOT NULL,
    cosmetic_name TEXT NOT NULL,
    cosmetic_type TEXT NOT NULL,
    PRIMARY KEY (user_id, cosmetic_id)
);

CREATE SCHEMA IF NOT EXISTS reskin;

CREATE TABLE IF NOT EXISTS reskin.guild_config (
    guild_id BIGINT PRIMARY KEY,
    status BOOLEAN NOT NULL DEFAULT TRUE,
    username VARCHAR(80),
    avatar_url TEXT,
    embed_color BIGINT
);

CREATE TABLE IF NOT EXISTS reskin.config (
    user_id BIGINT PRIMARY KEY,
    status BOOLEAN NOT NULL DEFAULT TRUE,
    username VARCHAR(80),
    avatar_url TEXT,
    embed_color BIGINT
);

CREATE TABLE IF NOT EXISTS reskin.webhook (
    status BOOLEAN NOT NULL DEFAULT TRUE,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    webhook_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE SCHEMA IF NOT EXISTS voicemaster;

CREATE TABLE IF NOT EXISTS voicemaster.config (
    guild_id BIGINT PRIMARY KEY,
    category_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    bitrate INTEGER,
    template TEXT,
    status_template TEXT
);

CREATE TABLE IF NOT EXISTS voicemaster.channel (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS logging (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    events TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS name_history (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username VARCHAR(32) NOT NULL,
    is_nickname BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS avatar_history (
    id SERIAL,
    user_id BIGINT NOT NULL,
    asset TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, asset)
);




CREATE SCHEMA IF NOT EXISTS monitor;

CREATE TABLE IF NOT EXISTS monitor.pubsub (
    id TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monitor.youtube (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    shorts BOOLEAN NOT NULL DEFAULT FALSE,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.tiktok (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    reposts BOOLEAN NOT NULL DEFAULT FALSE,
    lives BOOLEAN NOT NULL DEFAULT FALSE,
    template TEXT,
    live_template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.instagram (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    full_name TEXT NOT NULL,
    avatar_url TEXT,
    posts BOOLEAN NOT NULL DEFAULT TRUE,
    stories BOOLEAN NOT NULL DEFAULT TRUE,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.twitch (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.kick (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.twitter (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    retweets BOOLEAN NOT NULL DEFAULT TRUE,
    replies BOOLEAN NOT NULL DEFAULT TRUE,
    quotes BOOLEAN NOT NULL DEFAULT TRUE,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.soundcloud (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.beatstars (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.reddit (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.tumblr (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.letterboxd (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    template TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS monitor.pinterest (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    embeds BOOLEAN NOT NULL DEFAULT FALSE,
    board_id TEXT NOT NULL DEFAULT '0',
    board_name TEXT,
    PRIMARY KEY (guild_id, user_id, board_id)
);

CREATE SCHEMA IF NOT EXISTS worker;

CREATE TABLE IF NOT EXISTS worker.guilds (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    icon_hash TEXT,
    vanity_url_code TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS worker.messages (
    id BIGINT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    author_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE SCHEMA IF NOT EXISTS lastfm;

CREATE TABLE IF NOT EXISTS lastfm.config (
    user_id BIGINT PRIMARY KEY,
    username TEXT NOT NULL,
    session_key TEXT, 
    reactions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    last_sync TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    command TEXT,
    embed_mode TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS lastfm.artists (
    user_id BIGINT NOT NULL,
    artist CITEXT NOT NULL,
    plays BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, artist),
    FOREIGN KEY (user_id) REFERENCES lastfm.config(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lastfm.albums (
    user_id BIGINT NOT NULL,
    album CITEXT NOT NULL,
    artist CITEXT NOT NULL,
    plays BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, album, artist),
    FOREIGN KEY (user_id) REFERENCES lastfm.config(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lastfm.tracks (
    user_id BIGINT NOT NULL,
    track CITEXT NOT NULL,
    artist CITEXT NOT NULL,
    plays BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, track, artist),
    FOREIGN KEY (user_id) REFERENCES lastfm.config(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sticky (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS gallery (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE SCHEMA IF NOT EXISTS system;

CREATE TABLE IF NOT EXISTS system.welcome (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    delete_after INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS system.rejoin (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    delete_after INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS system.goodbye (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    delete_after INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS system.boost (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    delete_after INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS system.schedule (
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  template TEXT NOT NULL,
  interval INTEGER NOT NULL,
  next_run TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (guild_id, channel_id)
);

CREATE SCHEMA IF NOT EXISTS timer;

CREATE TABLE IF NOT EXISTS timer.nuke (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    interval INTERVAL NOT NULL,
    next_trigger TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS timer.task (
    id SERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS starboard (
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  self_star BOOLEAN NOT NULL DEFAULT TRUE,
  threshold INTEGER NOT NULL DEFAULT 3,
  emoji TEXT NOT NULL DEFAULT '⭐',
  PRIMARY KEY (guild_id, emoji)
);

CREATE TABLE IF NOT EXISTS starboard_entry (
  guild_id BIGINT NOT NULL,
  star_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY (guild_id, channel_id, message_id, emoji),
  FOREIGN KEY (guild_id, emoji) REFERENCES starboard (guild_id, emoji) ON DELETE CASCADE
);

CREATE SCHEMA IF NOT EXISTS snipe;

CREATE TABLE IF NOT EXISTS snipe.message (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    embeds JSONB NOT NULL DEFAULT '[]'::JSONB,
    attachments JSONB NOT NULL DEFAULT '[]'::JSONB,
    stickers JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS snipe.edited_message (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    embeds JSONB NOT NULL DEFAULT '[]'::JSONB,
    attachments JSONB NOT NULL DEFAULT '[]'::JSONB,
    stickers JSONB NOT NULL DEFAULT '[]'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    edited_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE SCHEMA IF NOT EXISTS economy;

CREATE TABLE IF NOT EXISTS economy.user (
    user_id BIGINT PRIMARY KEY,
    last_daily TIMESTAMP WITH TIME ZONE,
    last_worked TIMESTAMP WITH TIME ZONE,
    experience BIGINT NOT NULL DEFAULT 0,
    balance NUMERIC(400, 2) NOT NULL DEFAULT 0,
    wagered NUMERIC(400, 2) NOT NULL DEFAULT 0,
    net_profit NUMERIC(400, 2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS economy.rakeback (
    user_id BIGINT PRIMARY KEY,
    last_claimed TIMESTAMP WITH TIME ZONE,
    amount NUMERIC(400, 2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS economy.bet (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount NUMERIC(400, 2) NOT NULL,
    multiplier NUMERIC(400, 2) NOT NULL DEFAULT 0,
    payout NUMERIC(400, 2) NOT NULL DEFAULT 0,
    game TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- CREATE OR REPLACE FUNCTION NEXT_CASE(BIGINT) RETURNS BIGINT
--     LANGUAGE plpgsql
--     AS $$
-- DECLARE
--     next_id BIGINT;
-- BEGIN
--     SELECT MAX(id) INTO next_id FROM moderation.case WHERE guild_id = $1;
--     IF next_id IS NULL THEN RETURN 1; END IF;
--     RETURN next_id + 1;
-- END;
-- $$;

-- UNCOMMENT THIS BLOCK ON FIRST EXECUTION
-- CREATE OR REPLACE FUNCTION snipe.cleanup()
-- RETURNS TRIGGER AS $$
-- BEGIN
--     DELETE FROM snipe.message
--     WHERE guild_id = NEW.guild_id
--     AND id IN (
--         SELECT id
--         FROM snipe.message
--         WHERE guild_id = NEW.guild_id
--         ORDER BY created_at DESC
--         OFFSET 20
--     );
--     RETURN NEW;
-- END;
-- $$ LANGUAGE plpgsql;

-- DO $$
-- BEGIN
--     IF NOT EXISTS (
--         SELECT 1
--         FROM pg_trigger
--         WHERE tgname = 'snipe_cleanup'
--     ) THEN
--         CREATE TRIGGER snipe_cleanup
--         AFTER INSERT ON snipe.message
--         FOR EACH ROW
--         EXECUTE FUNCTION snipe.cleanup();
--     END IF;
-- END $$;
-- CREATE OR REPLACE FUNCTION snipe.edit_cleanup()
-- RETURNS TRIGGER AS $$
-- BEGIN
--     DELETE FROM snipe.edited_message
--     WHERE guild_id = NEW.guild_id
--     AND id IN (
--         SELECT id
--         FROM snipe.edited_message
--         WHERE guild_id = NEW.guild_id
--         ORDER BY edited_at DESC
--         OFFSET 20
--     );
--     RETURN NEW;
-- END;
-- $$ LANGUAGE plpgsql;

-- DO $$
-- BEGIN
--     IF NOT EXISTS (
--         SELECT 1
--         FROM pg_trigger
--         WHERE tgname = 'snipe_edit_cleanup'
--     ) THEN
--         CREATE TRIGGER snipe.edit_cleanup
--         AFTER INSERT ON snipe.edited_message
--         FOR EACH ROW
--         EXECUTE FUNCTION snipe.edit_cleanup();
--     END IF;
-- END $$;
