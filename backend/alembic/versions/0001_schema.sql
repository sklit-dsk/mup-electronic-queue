CREATE TABLE audit (
	id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36), 
	action VARCHAR(50) NOT NULL, 
	target_id VARCHAR(100) NOT NULL, 
	reason VARCHAR(300) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE auth_throttles (
	key VARCHAR(64) NOT NULL, 
	count INTEGER NOT NULL, 
	resets_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);
CREATE TABLE idempotency (
	key VARCHAR(180) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	result JSON NOT NULL, 
	PRIMARY KEY (key)
);
CREATE TABLE office_days (
	id VARCHAR(50) NOT NULL, 
	office_id VARCHAR(30) NOT NULL, 
	date DATE NOT NULL, 
	closed BOOLEAN NOT NULL, 
	next_number INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (office_id, date)
);
CREATE TABLE otp_challenges (
	id VARCHAR(36) NOT NULL, 
	phone VARCHAR(30) NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	attempts INTEGER NOT NULL, 
	consumed BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	phone VARCHAR(100) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	office_id VARCHAR(30) NOT NULL, 
	password_hash TEXT, 
	PRIMARY KEY (id), 
	UNIQUE (phone)
);
CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	encrypted_fields TEXT NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	verified_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	UNIQUE (fingerprint), 
	FOREIGN KEY(verified_by) REFERENCES users (id)
);
CREATE TABLE outbox (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(50) NOT NULL, 
	payload JSON NOT NULL, 
	attempts INTEGER NOT NULL, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	next_attempt TIMESTAMP WITH TIME ZONE NOT NULL, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
);
CREATE TABLE seats (
	id VARCHAR(80) NOT NULL, 
	day_id VARCHAR(50) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	ticket_id VARCHAR(36), 
	offer_id VARCHAR(36), 
	PRIMARY KEY (id), 
	UNIQUE (day_id, ordinal), 
	CHECK (ordinal BETWEEN 1 AND 100), 
	CHECK ((state = 'FREE' AND ticket_id IS NULL AND offer_id IS NULL) OR (state = 'HELD' AND offer_id IS NOT NULL AND ticket_id IS NULL) OR (state IN ('ALLOCATED','CONSUMED') AND ticket_id IS NOT NULL AND offer_id IS NULL)), 
	FOREIGN KEY(day_id) REFERENCES office_days (id), 
	UNIQUE (ticket_id), 
	UNIQUE (offer_id)
);
CREATE TABLE sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	csrf VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE day_claims (
	document_id VARCHAR(36) NOT NULL, 
	date DATE NOT NULL, 
	ticket_id VARCHAR(36), 
	offer_id VARCHAR(36), 
	state VARCHAR(20) NOT NULL, 
	PRIMARY KEY (document_id, date), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
);
CREATE TABLE tickets (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	day_id VARCHAR(50) NOT NULL, 
	service VARCHAR(30) NOT NULL, 
	number INTEGER NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	companion INTEGER NOT NULL, 
	"window" INTEGER, 
	original_match BOOLEAN NOT NULL, 
	version INTEGER NOT NULL, 
	calls INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (day_id, number), 
	CHECK (companion IN (0,1)), 
	CHECK (state NOT IN ('CALLED','IN_SERVICE') OR "window" IS NOT NULL), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(day_id) REFERENCES office_days (id)
);
CREATE TABLE uploads (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	file_key VARCHAR(60), 
	status VARCHAR(30) NOT NULL, 
	encrypted_result TEXT, 
	error VARCHAR(200), 
	processing_at TIMESTAMP WITH TIME ZONE, 
	demo BOOLEAN NOT NULL, 
	document_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
);
CREATE TABLE waiting (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	day_id VARCHAR(50) NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	companion INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	ticket_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (companion IN (0,1)), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(day_id) REFERENCES office_days (id), 
	FOREIGN KEY(ticket_id) REFERENCES tickets (id)
);
CREATE INDEX ix_documents_owner_id ON documents (owner_id);
CREATE INDEX ix_seats_day_id ON seats (day_id);
CREATE UNIQUE INDEX one_ticket_per_window ON tickets (day_id, "window") WHERE state IN ('CALLED','IN_SERVICE');
CREATE INDEX ix_tickets_owner_id ON tickets (owner_id);
CREATE INDEX ix_tickets_day_id ON tickets (day_id);
CREATE INDEX ix_tickets_document_id ON tickets (document_id);
CREATE UNIQUE INDEX one_waiting_per_document_day ON waiting (document_id, day_id) WHERE state IN ('WAITING','OFFERED');
