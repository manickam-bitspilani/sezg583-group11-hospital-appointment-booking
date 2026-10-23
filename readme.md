# Hospital Appointment Booking — Microservices

Scalable Services (SEZG583) · Assignment 1

Patients book limited OPD slots with doctors. Built as 4 independent Node.js services, each with its own PostgreSQL database.

## Services

| Service | Port | Owner |
| --- | --- | --- |
| patient-service | 3001 | Mr. D |
| slot-service | 3002 | Sripriya |
| booking-service | 3003 | Manickam |
| notification-service | 3004 | TBD |

## Tech stack

Node.js · Express · PostgreSQL · RabbitMQ

## Run locally

```bash
docker compose up -d          # Postgres + RabbitMQ

cd services/<service-name>
cp .env.example .env
npm install
npm start
```

Health check: `GET http://localhost:<port>/health`

## Team rules

- Commit from your own GitHub account, only inside your own service folder.
- One branch per service; merge to `main` by pull request.
