-- One database and one login role per service (database-per-service).
-- Each role can connect only to its own database.
-- Run as a PostgreSQL superuser:  psql -U postgres -f scripts/setup_databases.sql
-- Passwords here are local-development defaults; they match each service's .env.example.

CREATE ROLE patient_svc      LOGIN PASSWORD 'patient_pw';
CREATE ROLE slot_svc         LOGIN PASSWORD 'slot_pw';
CREATE ROLE booking_svc      LOGIN PASSWORD 'booking_pw';
CREATE ROLE notification_svc LOGIN PASSWORD 'notification_pw';

CREATE DATABASE patient_db      OWNER patient_svc;
CREATE DATABASE slot_db         OWNER slot_svc;
CREATE DATABASE booking_db      OWNER booking_svc;
CREATE DATABASE notification_db OWNER notification_svc;

REVOKE CONNECT ON DATABASE patient_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE slot_db         FROM PUBLIC;
REVOKE CONNECT ON DATABASE booking_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE notification_db FROM PUBLIC;
