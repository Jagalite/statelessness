#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."

# This source identity is independent of earlier queued jobs using live dist/.
(cd .. && shasum -a 256 -c typescript/receipts/n05/final-focused-source.sha256)
node node_modules/typescript/bin/tsc -p tsconfig.json --outDir dist/n05-final-focused/dist
mkdir -p dist/n05-final-focused/test
cp test/guided.test.mjs dist/n05-final-focused/test/guided.test.mjs
node --test --test-name-pattern 'arbitrary feedback throwables|ordinary fuzz preserves|malformed Error[.]message' dist/n05-final-focused/test/guided.test.mjs
node --test ../scripts/native-guided-consumers/typescript/seal.test.mjs
node --check ../scripts/native-guided-consumers/typescript/run.mjs
(cd .. && shasum -a 256 -c typescript/receipts/n05/final-focused-source.sha256)
