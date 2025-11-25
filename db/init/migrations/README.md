# Database Migrations

This directory contains database migration scripts that should be run on existing databases when schema changes are made.

## Running Migrations

### Manual Migration (Recommended for now)

Connect to the database and run the migration:

```bash
# Connect to database
docker exec -it pgx_db psql -U cpic_user -d cpic_db

# Run the migration
\i /docker-entrypoint-initdb.d/migrations/{migration}}

# Or run directly:
docker exec -it pgx_db psql -U cpic_user -d cpic_db -f /docker-entrypoint-initdb.d/migrations/{migration}
```

### Check Current Schema


## Migration Files

...

