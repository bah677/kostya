-- RSVP на закрытую встречу встречающих (одноразовые кампании).
CREATE TABLE IF NOT EXISTS greeter_meeting_rsvp (
    meeting_key   TEXT NOT NULL,
    user_id       BIGINT NOT NULL,
    response      TEXT NOT NULL CHECK (response IN ('coming', 'cant')),
    responded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (meeting_key, user_id)
);

CREATE INDEX IF NOT EXISTS greeter_meeting_rsvp_meeting_resp
    ON greeter_meeting_rsvp (meeting_key, response);
