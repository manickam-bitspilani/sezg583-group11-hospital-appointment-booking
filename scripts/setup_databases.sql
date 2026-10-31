-- One database and one login role per service (database-per-service).
-- Each role can connect only to its own database.
-- Passwords are psql variables, never stored in this file. Normally run through
-- ./scripts/setup_local.sh, which passes the passwords from secrets/*.txt:
--   psql -U postgres -v patient_pw=... -v slot_pw=... -v booking_pw=... \
--        -v notification_pw=... -f scripts/setup_databases.sql

\set ON_ERROR_STOP on

CREATE ROLE patient_svc      LOGIN PASSWORD :'patient_pw';
CREATE ROLE slot_svc         LOGIN PASSWORD :'slot_pw';
CREATE ROLE booking_svc      LOGIN PASSWORD :'booking_pw';
CREATE ROLE notification_svc LOGIN PASSWORD :'notification_pw';

CREATE DATABASE patient_db      OWNER patient_svc;
CREATE DATABASE slot_db         OWNER slot_svc;
CREATE DATABASE booking_db      OWNER booking_svc;
CREATE DATABASE notification_db OWNER notification_svc;

REVOKE CONNECT ON DATABASE patient_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE slot_db         FROM PUBLIC;
REVOKE CONNECT ON DATABASE booking_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE notification_db FROM PUBLIC;
