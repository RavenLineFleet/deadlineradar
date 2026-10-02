-- Companion to 0081_attribution_daily.sql: records which channel (if any)
-- first brought a subscriber to the site, so signups can be attributed the
-- same way traffic is. NULL for every row before this build and for any
-- signup with no ?src= in its first-touch history -- never a guess, same
-- "don't fabricate, disclose the gap instead" posture as every other
-- optional column on this table.
ALTER TABLE subscribers ADD COLUMN first_touch_src TEXT;
