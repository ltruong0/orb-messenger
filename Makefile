# Orb Messenger. Targets listed in CLAUDE.md; add them as each phase lands.
# Python tooling runs through uv (pins Python 3.12 + locked deps).
.PHONY: fw-release contract-test broker-test broker-check sim-test hub-test test lint fmt mosquitto-passwd hub-secrets sim sim-ui hub-run image image-push set-image fonts deploy deploy-diff deploy-status fw-secrets fw-build fw-flash fw-monitor

UV ?= uv
IMAGE ?= ghcr.io/ltruong0/orb-messenger/msg-hub
PLATFORM ?= linux/amd64   # the k3s node; building on an Apple Silicon Mac gives arm64 otherwise
TAG ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' hub/pyproject.toml)
EMOJI_FONT_URL ?= https://github.com/googlefonts/noto-emoji/raw/v2.047/fonts/NotoColorEmoji.ttf
EMOJI_FONT_CACHE ?= $(HOME)/.cache/orb-messenger/fonts/NotoColorEmoji.ttf
KUSTOMIZE_DIR ?= deploy/k8s/overlays/homelab
PIO ?= $(shell command -v pio 2>/dev/null || echo $(HOME)/.local/bin/pio)   # uv tool install platformio
FW_PORT ?=

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
	$(UV) run pytest tests/test_orb_sim.py tests/test_orb_sim_web.py

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

image:                    ## build the msg-hub container image (context = repo root; TAG = hub version)
	docker buildx build --platform $(PLATFORM) --load -f hub/Dockerfile -t $(IMAGE):$(TAG) .

image-push: image         ## push it to GHCR (docker login ghcr.io first)
	docker push $(IMAGE):$(TAG)

set-image:                ## pin the homelab overlay to $(TAG)
	sed -i '' 's|^\(    newTag: \).*|\1$(TAG)|' $(KUSTOMIZE_DIR)/kustomization.yaml
	@grep -n newTag $(KUSTOMIZE_DIR)/kustomization.yaml

fonts:                    ## fetch NotoColorEmoji.ttf for local sticker rendering (~10 MB, one-off)
	@mkdir -p $(dir $(EMOJI_FONT_CACHE))
	curl -sSL -o $(EMOJI_FONT_CACHE) $(EMOJI_FONT_URL)
	@file $(EMOJI_FONT_CACHE)

IDS ?= orb-sim1,orb-sim2
PORT ?= 8090
sim-ui:                   ## browser UI with mock orb screens: make sim-ui IDS=orb-sim1,orb-sim2 PORT=8090
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; $(UV) run orb-sim --web $(PORT) --ids $(IDS) $(SIM_ARGS)

lint:
	$(UV) run ruff check . && $(UV) run ruff format --check .

fmt:
	$(UV) run ruff check --fix . && $(UV) run ruff format .

mosquitto-passwd:         ## write the gitignored passwd file for the homelab overlay from .env
	@test -f .env || { echo ".env missing: copy .env.example and set MQTT_*_PASSWORD"; exit 1; }
	@set -a; . ./.env; set +a; \
	  $(UV) run python tools/mosquitto_passwd.py $(KUSTOMIZE_DIR)/secrets/passwd \
	    "msg-hub=$$MQTT_HUB_PASSWORD" "orb=$$MQTT_ORB_PASSWORD"

hub-secrets:              ## write the gitignored hub.env for the homelab overlay from .env
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; \
	  test -n "$$HUB_PIN" -a -n "$$SESSION_SECRET" -a -n "$$MQTT_HUB_PASSWORD" || { echo "set HUB_PIN, SESSION_SECRET, MQTT_HUB_PASSWORD in .env"; exit 1; }; \
	  umask 077; printf 'HUB_PIN=%s\nSESSION_SECRET=%s\nMQTT_PASSWORD=%s\n' "$$HUB_PIN" "$$SESSION_SECRET" "$$MQTT_HUB_PASSWORD" > $(KUSTOMIZE_DIR)/secrets/hub.env
	@echo "wrote $(KUSTOMIZE_DIR)/secrets/hub.env"

deploy-status:            ## rollout status + pods in the orb namespace
	kubectl -n orb rollout status deploy/hub --timeout=120s
	kubectl -n orb get pods,svc,ingress,pvc

deploy-diff:
	kubectl diff -k $(KUSTOMIZE_DIR) || true

deploy:                   ## kubectl apply -k deploy/k8s/overlays/homelab (needs secrets/passwd + secrets/hub.env)
	@test -f $(KUSTOMIZE_DIR)/secrets/passwd || { echo "run make mosquitto-passwd first"; exit 1; }
	@test -f $(KUSTOMIZE_DIR)/secrets/hub.env || { echo "run make hub-secrets first"; exit 1; }
	kubectl apply -k $(KUSTOMIZE_DIR)

fw-secrets:               ## write firmware/secrets.ini (gitignored) from .env: broker password, optional Wi-Fi
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; \
	  umask 077; { \
	    echo "; generated by make fw-secrets from .env; do not commit"; \
	    echo "[secrets]"; \
	    echo "build_flags ="; \
	    echo "    -DORB_MQTT_HOST=\\\"$${MQTT_HOST:-mqtt.marcellolab.com}\\\""; \
	    echo "    -DORB_MQTT_PORT=$${MQTT_PORT:-1883}"; \
	    echo "    -DORB_MQTT_PASS=\\\"$$MQTT_ORB_PASSWORD\\\""; \
	    [ -n "$$WIFI_SSID" ] && echo "    -DORB_WIFI_SSID=\\\"$$WIFI_SSID\\\""; \
	    [ -n "$$WIFI_PASS" ] && echo "    -DORB_WIFI_PASS=\\\"$$WIFI_PASS\\\""; \
	    [ -n "$$ORB_LED_PIN" ] && echo "    -DORB_LED_PIN=$$ORB_LED_PIN"; \
	    true; } > firmware/secrets.ini
	@echo "wrote firmware/secrets.ini ($$(grep -c ORB_ firmware/secrets.ini) flags)"

fw-build:                 ## compile the orb firmware (pioarduino, Arduino core 3.x)
	$(PIO) run -d firmware -e orb

fw-flash:                 ## build + flash over USB (FW_PORT=/dev/cu.usbmodemXXX to pick a port)
	$(PIO) run -d firmware -e orb -t upload $(if $(FW_PORT),--upload-port $(FW_PORT),)

HUB_URL ?= https://orb.marcellolab.com
fw-release: fw-build       ## upload the build to the hub for OTA (version from platformio.ini, model from ORB_BOARD)
	@test -f .env || { echo ".env missing"; exit 1; }
	@set -a; . ./.env; set +a; \
	  ver=$$(sed -n 's/.*-DORB_FW_VERSION=\\"\([^"\\]*\)\\".*/\1/p' firmware/platformio.ini | head -1); \
	  board=$$(sed -n 's/.*-DORB_BOARD=\([0-9]*\).*/\1/p' firmware/platformio.ini | head -1); \
	  case $$board in 175) model=ws-s3-amoled-1.75;; *) model=ws-s3-amoled-1.43;; esac; \
	  echo "releasing $$model $$ver to $(HUB_URL)"; \
	  jar=$$(mktemp); trap 'rm -f $$jar' EXIT; \
	  printf '{"user":"lee","pin":"%s"}' "$$HUB_PIN" | curl -sk -c $$jar -o /dev/null -w "login %{http_code}\n" \
	    -H 'content-type: application/json' --data @- $(HUB_URL)/api/session; \
	  curl -sk -b $$jar -F file=@firmware/.pio/build/orb/firmware.bin -F version=$$ver -F model=$$model $(HUB_URL)/api/firmware; echo

fw-monitor:               ## serial log at 115200 with the exception decoder
	$(PIO) device monitor -d firmware -e orb $(if $(FW_PORT),--port $(FW_PORT),)
