.PHONY: setup up down restart logs status build pull

PIPES := pipe/spotify pipe/airplay

# Создать FIFO и каталоги перед первым запуском
setup:
	@mkdir -p pipe music config api-data
	@for p in $(PIPES); do [ -p $$p ] || mkfifo $$p; done
	@chmod 666 $(PIPES)
	@[ -f config/device_name ] || printf 'spotineck' > config/device_name
	@[ -f .env ] || cp .env.example .env
	@echo "ok. дальше: make up"

up:
	docker compose up -d --build

build:
	docker compose build

pull:
	docker compose pull

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

status:
	docker compose ps
