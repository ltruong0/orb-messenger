# Orb Messenger. Targets listed in CLAUDE.md; add them as each phase lands.
# Python tooling runs through uv (pins Python 3.12 + locked deps).
.PHONY: contract-test broker-test broker-check test lint fmt mosquitto-passwd deploy deploy-diff

UV ?= uv
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

test: contract-test broker-test

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
