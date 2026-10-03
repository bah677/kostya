cd /path/to/avatar_kostya
./scripts/init_fresh_database.sh


# Основная БД аватара Кости (не путать с Julia `avatar_db`):
#   DB_NAME=avatar_db_kostya
#
# Создание с владельцем avatar_db_user (нужен sudo/postgres):
sudo -u postgres psql -c "CREATE DATABASE avatar_db_kostya OWNER avatar_db_user;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE avatar_db_kostya TO avatar_db_user;"

# Если база уже создана другой ролью — по желанию передать владельца:
# sudo -u postgres psql -c "ALTER DATABASE avatar_db_kostya OWNER TO avatar_db_user;"
# sudo -u postgres psql -d avatar_db_kostya -c "REASSIGN OWNED BY miron_user TO avatar_db_user;"
