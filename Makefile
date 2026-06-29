.PHONY: setup up down restart logs status build pull

PIPES := pipe/spotify pipe/airplay

# Create FIFOs and directories before the first run
setup:
	@mkdir -p pipe music config api-data
	@for p in $(PIPES); do [ -p $$p ] || mkfifo $$p; done
	@chmod 666 $(PIPES)
	@[ -f config/device_name ] || printf 'spotineck' > config/device_name
	@[ -f .env ] || cp .env.example .env
	@echo "ok. next: make up"

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
