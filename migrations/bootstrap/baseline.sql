-- NAFSIY original baseline schema for revision 69e1d999085f.
-- Static reviewed snapshot; never execute against a nonempty database.

CREATE TABLE ai_rate_limits (
	bucket_key VARCHAR(80) NOT NULL,
	count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (bucket_key)
);

CREATE TABLE auth_users (
	id VARCHAR(32) NOT NULL,
	email VARCHAR(320) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_auth_users_email ON auth_users (email);

CREATE TABLE surahs (
	id SERIAL NOT NULL,
	name_ar VARCHAR(200) NOT NULL,
	name_en VARCHAR(200) NOT NULL,
	ayah_count INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE ayahs (
	id SERIAL NOT NULL,
	surah_id INTEGER NOT NULL,
	number INTEGER NOT NULL,
	arabic_text TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (surah_id, number),
	FOREIGN KEY(surah_id) REFERENCES surahs (id)
);

CREATE TABLE chat_sessions (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	title VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES auth_users (id) ON DELETE CASCADE
);

CREATE INDEX ix_chat_sessions_user_id ON chat_sessions (user_id);

CREATE INDEX ix_chat_sessions_user_updated ON chat_sessions (user_id, updated_at);

CREATE TABLE user_bookmarks (
	id VARCHAR(32) NOT NULL,
	user_id VARCHAR(32) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	reference VARCHAR(120) NOT NULL,
	title VARCHAR(200) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (user_id, kind, reference),
	CONSTRAINT ck_bookmark_kind CHECK (kind IN ('quran', 'hadith')),
	FOREIGN KEY(user_id) REFERENCES auth_users (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_bookmarks_user_id ON user_bookmarks (user_id);

CREATE TABLE chat_messages (
	id VARCHAR(32) NOT NULL,
	session_id VARCHAR(32) NOT NULL,
	role VARCHAR(10) NOT NULL,
	content TEXT NOT NULL,
	sources_json TEXT NOT NULL,
	position INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (session_id, position),
	CONSTRAINT ck_chat_message_role CHECK (role IN ('user', 'assistant')),
	FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
);

CREATE INDEX ix_chat_messages_session_id ON chat_messages (session_id);

CREATE TABLE translations (
	id SERIAL NOT NULL,
	ayah_id INTEGER NOT NULL,
	language VARCHAR(10) NOT NULL,
	edition VARCHAR(100) NOT NULL,
	translator VARCHAR(200) NOT NULL,
	text TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (ayah_id, edition),
	FOREIGN KEY(ayah_id) REFERENCES ayahs (id)
);
