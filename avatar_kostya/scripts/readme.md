cd /path/to/avatar_kostya
./scripts/init_fresh_database.sh


# Создание базы данных с владельцем-пользователем
sudo -u postgres psql -c "CREATE DATABASE avatar_db_anna OWNER avatar_db_user;"

# Назначение всех привилегий на базу данных пользователю (опционально)
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE avatar_db_anna TO avatar_db_user;"