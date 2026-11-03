# Orb Messenger. Targets listed in CLAUDE.md; add them as each phase lands.
# Python tooling runs through uv (pins Python 3.12 + locked deps).
.PHONY: contract-test broker-test broker-check sim-test hub-test test lint fmt mosquitto-passwd sim hub-run image fonts deploy deploy-diff

UV ?= uv
IMAGE ?= ghcr.io/ltruong0/orb-messenger/msg-hub
TAG ?= dev
EMOJI_FONT_URL ?= https://github.com/googlefonts/noto-emoji/raw/v2.047/fonts/NotoColorEmoji.ttf
EMOJI_FONT_CACHE ?= $(HOME)/.cache/orb-messenger/fonts/NotoColorEmoji.ttf
KUSTOMIZE_DIR ?= deploy/k8s/overlays/homelab

contract-test:            ## validate contracts/examples against the schemas
	$(UV) run python contracts/validate.py

broker-test:              ## ACL + persistence tests against Mosquitto in Docker
	$(UV) run pytest tests/test_broker_acl.py

broker-check:             ## same tests against the deployed broker (restarts the pod once)
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; \
	  ORB_BROKER_HOST=$${ORB_BROKER_HOST:-192.168.40.129} ORB_BROKER_PORT=$$MQTT_PORT \
	  $(UV) run pytest tests/test_broker_acl.py

sim-test:                 ## orb-sim against Mosquitto in Docker
	$(UV) run pytest tests/test_orb_sim.py

hub-test:                 ## msg-hub unit + integration tests (Mosquitto in Docker, two orb-sims)
	$(UV) run pytest hub/tests

test: contract-test broker-test sim-test hub-test

ID ?= orb-sim1
sim:                      ## run an orb simulator: make sim ID=orb-sim1 (broker + password from .env)
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; $(UV) run orb-sim --id $(ID) $(SIM_ARGS)

hub-run:                  ## run msg-hub locally against MQTT_HOST (state in hub/data, gitignored)
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; \
	  MQTT_PASSWORD=$$MQTT_HUB_PASSWORD DATA_DIR=$${DATA_DIR:-hub/data} \
	  MEDIA_BASE_URL=$${MEDIA_BASE_URL:-http://$$(ipconfig getifaddr en0 2>/dev/null || hostname):8080} \
	  $(UV) run msg-hub

image:                    ## build the msg-hub container image (context = repo root)
	docker build -f hub/Dockerfile -t $(IMAGE):$(TAG) .

fonts:                    ## fetch NotoColorEmoji.ttf for local sticker rendering (~10 MB, one-off)
	@mkdir -p $(dir $(EMOJI_FONT_CACHE))
	curl -sSL -o $(EMOJI_FONT_CACHE) $(EMOJI_FONT_URL)
	@file $(EMOJI_FONT_CACHE)

lint:
	$(UV) run ruff check . && $(UV) run ruff format --check .

fmt:
	$(UV) run ruff check --fix . && $(UV) run ruff format .

mosquitto-passwd:         ## write the gitignored passwd file for the homelab overlay from .env
	@test -f .env || { echo ".env missing: copy .env.example and set MQTT_*_PASSWORD"; exit 1; }
	@set -a; . ./.env; set +a; \
	  $(UV) run python tools/mosquitto_passwd.py $(KUSTOMIZE_DIR)/secrets/passwd \
	    "msg-hub=$$MQTT_HUB_PASSWORD" "orb=$$MQTT_ORB_PASSWORD"

deploy-diff:
	kubectl diff -k $(KUSTOMIZE_DIR) || true

deploy:                   ## kubectl apply -k deploy/k8s/overlays/homelab
	kubectl apply -k $(KUSTOMIZE_DIR)
