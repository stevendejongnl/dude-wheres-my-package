# CHANGELOG

<!-- version list -->

## v1.29.0 (2026-04-14)

### Features

- **ui**: Click-to-copy JS snippets and console cookie export
  ([`d6bb180`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/d6bb18079ddfb5788d3e0f2b9624093202b61f73))


## v1.28.0 (2026-04-14)

### Features

- **notifications**: Include event description in status-change notifications
  ([`5f901cf`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/5f901cf3730234d4c04fc59c264851c9541809de))


## v1.27.0 (2026-04-14)

### Features

- **amazon**: Cookies fallback when bot detection blocks Playwright login
  ([`6dec688`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/6dec688ba461b851311ee0003c1b48e5cf05a43c))


## v1.26.0 (2026-04-14)

### Features

- **accounts**: Add postal_code field so account-synced packages get public tracking
  ([`611fb2f`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/611fb2f3b954f6c57d124647820a58d298c50fec))


## v1.25.0 (2026-04-14)

### Features

- **dhl**: Rich event timeline via DHL Unified Tracking API with Playwright fallback
  ([`33efabb`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/33efabb6cab75417dcc04466e111bb09c240da68))


## v1.24.0 (2026-04-14)

### Features

- **ui**: Collapsible Details section and Delete button on package cards
  ([`fb2142d`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/fb2142d6e9c541921bf2329265eefb1ae5a747ed))


## v1.23.0 (2026-04-14)

### Features

- **dpd**: Public tracking via postal-code verification (no login needed)
  ([`e784b51`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/e784b518c35b11b20acdb8d9fbee24fc2d4c3399))


## v1.22.0 (2026-04-14)

### Features

- **dpd,ui**: Skip Playwright validation for DPD and add per-package refresh button
  ([`57a6bed`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/57a6bed921f11e46e3001d070998666cd920c1b9))


## v1.21.3 (2026-04-14)

### Bug Fixes

- **ui**: Target form with hx-indicator so overlay actually triggers
  ([`86b690b`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/86b690b88ffb9af1a1c6f63bf2eeec22a9e6c35a))


## v1.21.2 (2026-04-14)

### Bug Fixes

- **dpd**: Detect expired Keycloak session that degrades to guest mode
  ([`9989390`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/9989390a5dadbbe115d26e541b6e6b5109472f87))


## v1.21.1 (2026-04-13)

### Bug Fixes

- **browser**: Include page url, title, and body snippet on selector-miss
  ([`175d673`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/175d673f987c55ce78f3d98c25e09fe119441ee8))


## v1.21.0 (2026-04-13)

### Bug Fixes

- **amazon**: Handle password-only, MFA-entry, and consent-banner cases in login
  ([`119ccf5`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/119ccf5ce0098b344ff1da87fc184da117ee102f))

### Features

- **ui**: Blur-and-spinner loader overlay for long-running form actions
  ([`3b58ab7`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/3b58ab7da3749f0ecb02291a24a3f4cede8d59f4))


## v1.20.1 (2026-04-13)

### Bug Fixes

- **dhl,k8s**: Accept totp_secret kwarg on DHL.login and raise resource limits for playwright
  ([`9696b63`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/9696b6395fd06ab924d333d1e8444c63b2f5d1fc))

### Build System

- **release**: Keep kubernetes/deployment.yaml image tag in sync via semantic-release
  ([`49156bd`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/49156bd024e4ce6efc215f05866b8b8be3e0295c))

### Code Style

- **repo**: Wrap long INSERT INTO packages line under 120 cols
  ([`90fa678`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/90fa678d172e480b42f321c503741e891bec1e8d))

### Documentation

- **readme**: Document web UI, auth flow, notifications API, and missing env vars
  ([`5818561`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/5818561a6549d5cfcf78fc61241293603f4f9ea4))

### Refactoring

- **tracking**: Unify refresh loop so packages survive dropping off account lists
  ([`a8e79f0`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/a8e79f0409aa51e5a0c42b81d162f7caebd550fa))


## v1.20.0 (2026-04-13)

### Features

- **packages**: Add track-a-package modal and hide GLS from accounts page
  ([`c21f51a`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/c21f51a63094647bc678605227470ad7ce79f63e))


## v1.19.0 (2026-04-13)

### Features

- **accounts**: Add edit-account flow with pre-filled form
  ([`f178f72`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/f178f72972cfe56602ce5dfa72bb79c368e73190))


## v1.18.0 (2026-04-13)

### Features

- **dpd**: Auto-refreshing cookies mode with stealth and real Chrome
  ([`8f34439`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/8f3443996b98430ac424907a5b6b4191665532dc))


## v1.17.3 (2026-04-13)

### Bug Fixes

- **ci**: Drop --auto from addon bump PR merge
  ([`125619d`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/125619dfb4eecfff4ab8475b5e29747ddb17ad19))


## v1.17.2 (2026-04-13)

### Bug Fixes

- **ui**: Enable Save button after successful test in add-account form
  ([`05dedc1`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/05dedc1c8dac01044a273a3d5a369599b39bb367))

### Continuous Integration

- Auto-open PR in addon repo on new dwmp release
  ([`499a73e`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/499a73e90f8a883f893031f6fb334670b2453d7d))


## v1.17.1 (2026-04-13)

### Bug Fixes

- Don't break StaticFiles mount when ingress prefix is set
  ([`c71f69d`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/c71f69de686cdcfe0127ccb3292d9f8605bf028a))


## v1.17.0 (2026-04-13)

### Documentation

- Cross-reference HA addon and integration
  ([`a5403f7`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/a5403f70c25482c795bdce4b0ee87fc519b50f08))

### Features

- **ui**: Easy-add account form on accounts page
  ([`17bb717`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/17bb717b5c34dad9d8f3554922dd27d9eea50df7))


## v1.16.0 (2026-04-13)

### Features

- Support HA ingress path prefix via X-Ingress-Path header
  ([`cde13c3`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/cde13c3dfae5916b163538e282c7e25c9873e9fd))


## v1.15.2 (2026-04-12)

### Bug Fixes

- **gls**: Remove unused sender variable
  ([`d0a07cb`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/d0a07cbdb38f2cb4c4d94c53e40fd65f742295cc))

- **gls**: Use correct apm.gls.nl API with postal code requirement
  ([`caddf03`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/caddf039f5717d509807b3dc1229c1eecb757a51))


## v1.15.1 (2026-04-12)

### Bug Fixes

- **ci**: Add retry mechanism for Docker push to GHCR
  ([`13c7398`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/13c7398506226cba55e249e4147516afd891d5ca))


## v1.15.0 (2026-04-12)

### Features

- **carriers**: Add GLS tracking support
  ([`85e807e`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/85e807e5f43f6be8760af00287171de33340b9de))


## v1.14.6 (2026-04-12)

### Bug Fixes

- **ci**: Build Docker in release job to fix version mismatch
  ([`549c232`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/549c232cc1183f297cb1bcb7d2b0652e1f5c2683))


## v1.14.5 (2026-04-12)

### Bug Fixes

- **ci**: Stage uv.lock in build_command so it's included in release commit
  ([`286d99e`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/286d99e1089071a95e924ddd04ba72aa76453f1d))


## v1.14.4 (2026-04-12)

### Bug Fixes

- **dwmp**: Resize icon to 256x256 for HA brands compatibility
  ([`169ed9b`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/169ed9b1fce22eb0947753492199748bcb03ae77))


## v1.14.3 (2026-04-12)

### Bug Fixes

- **ci**: Use build_command to sync uv.lock during release
  ([`62b93ed`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/62b93ed7b6be43c518fac46f7f9b6bb6e9db9a5f))


## v1.14.2 (2026-04-12)

### Bug Fixes

- **ci**: Configure git identity and force-push amended release commit
  ([`62f862f`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/62f862f045a861a5e6f9b4c9c8eebd99e7cda40c))


## v1.14.1 (2026-04-12)

### Bug Fixes

- **ci**: Sync uv.lock version during release to fix reported version
  ([`eeca44a`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/eeca44af8f8e1c7a8ccc39b4496652fafe7dd24c))


## v1.14.0 (2026-04-12)

### Features

- **api**: Add POST /api/v1/auth/token endpoint for machine-to-machine auth
  ([`e8392b0`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/e8392b0f4008bbdacb9be5b8d4b9c6a995d6e323))


## v1.13.0 (2026-04-12)

### Features

- **ui**: Auto-reload page when new version is deployed
  ([`bfa4dbc`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/bfa4dbc249f32a0a696ca36422494039f97fd234))


## v1.12.4 (2026-04-12)

### Bug Fixes

- **ui**: Convert timestamps to configured TZ (default Europe/Amsterdam)
  ([`38c2114`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/38c21145b4a2ba3cb310ba785785095d4c764747))


## v1.12.3 (2026-04-12)

### Bug Fixes

- **ui**: Fixed-width carrier labels for alignment, each detail on its own line
  ([`89e6b91`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/89e6b91916be2c70ade469dd84d1ce559f1d71fb))


## v1.12.2 (2026-04-12)

### Bug Fixes

- **ui**: Move sender below tracking number, add first event date, align carrier labels
  ([`f51fb0b`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/f51fb0b860e9a37f74a3d3bd15dd751e9d0468f9))


## v1.12.1 (2026-04-12)

### Bug Fixes

- **docker**: Install project after copying src so version metadata is correct, move API link to
  footer
  ([`e475f06`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/e475f062166da55af2dc4f011c5b67fad5fec948))


## v1.12.0 (2026-04-12)

### Features

- **ts**: Convert inline JS to TypeScript with Vitest tests, add ruff linting to CI
  ([`5ca85e8`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/5ca85e887bf1e743815273029ecb82f9ee41f51b))


## v1.11.0 (2026-04-12)

### Features

- **ui**: Add footer with GitHub repo and MadeBySteven links
  ([`a9fae11`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/a9fae11decce9d2228d84918d1cd83d8d9fb8da1))


## v1.10.3 (2026-04-12)

### Bug Fixes

- **notifications**: Auto-mark all read on page visit, remove read buttons
  ([`2c03ce5`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/2c03ce52d24b63202a9fae6e1acfb061db7c309a))

### Documentation

- Add Amazon carrier setup and update API reference
  ([`acb030a`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/acb030a0fc844caaf1c63ca1d184f21293c92ca7))


## v1.10.2 (2026-04-12)

### Bug Fixes

- **ui**: Skip dates and status text in sender extraction, bold From: label
  ([`546c4ab`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/546c4aba407afbcf115e6d2a979b0d1ac9693562))


## v1.10.1 (2026-04-12)

### Bug Fixes

- **amazon**: Treat future-tense 'wordt bezorgd' as in-transit, sort packages by last event
  ([`a1b576f`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/a1b576f74a7bacc81aaac9c626367f4b1cf5d7f8))


## v1.10.0 (2026-04-12)

### Features

- **amazon**: Automate login and sync with Playwright browser automation
  ([`35d7bda`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/35d7bdaae6c03351a390104c4cd10e6496c58b5d))


## v1.9.4 (2026-04-12)

### Bug Fixes

- **dpd**: Store sender name as PRE_TRANSIT event so 'from' line displays correctly
  ([`3ecb9fd`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/3ecb9fd850f8fb9daf342280594d76b16c842570))


## v1.9.3 (2026-04-12)

### Bug Fixes

- **amazon**: Use browser-captured HTML like DPD and fix UTF-8 encoding in Docker
  ([`4ac94ce`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/4ac94ceb11f659277723238ad937ca58d49e2410))


## v1.9.2 (2026-04-12)

### Bug Fixes

- **k8s**: Pin deployment to 1.9.1 (tag that exists in GHCR)
  ([`f17563c`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/f17563cfa8057c7dcadd1f564007aede6aa5f8d7))


## v1.9.1 (2026-04-12)

### Bug Fixes

- **ci**: Add explicit packages:write permission to docker job and use repository_owner for GHCR
  login
  ([`f90289e`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/f90289e3496b4490630c19bc4a2e091ba7ae4bb6))


## v1.9.0 (2026-04-12)

### Features

- **notifications**: Notify user when carrier auth expires
  ([`11f5f5e`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/11f5f5e81b05f6959d26dbe367329957c5159f78))


## v1.8.0 (2026-04-12)

### Features

- **amazon**: Add Amazon.nl carrier with session cookie auth and enable Keel auto-updates
  ([`56dc398`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/56dc398fde50fb6b6b107fb3ccc8cf6690aff0e7))


## v1.7.0 (2026-04-12)

### Features

- **ui**: Add notification center with unread badge and browser push notifications
  ([`fd111af`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/fd111afac5e349c216b6b34656e6d5d7b5e7f0b9))


## v1.6.1 (2026-04-12)

### Bug Fixes

- Read API version from package metadata instead of hardcoded
  ([`0650367`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/0650367ac086109bd6a12237b178701885f80812))


## v1.6.0 (2026-04-12)

### Features

- **ui**: Mobile responsive, hide nav on login, protect API with auth middleware
  ([`d47c678`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/d47c678c35c1bb0abbb4bbf7fea6f24a26002c17))


## v1.5.2 (2026-04-12)

### Bug Fixes

- **ui**: Larger header icon (48px CSS, 96px retina)
  ([`fb6023d`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/fb6023d54fbe9f826f7c118ae2d69e28314df1c4))


## v1.5.1 (2026-04-12)

### Bug Fixes

- **ui**: Larger header icon, remove DWMP text, version badge pill
  ([`67929e9`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/67929e9e83f203277ad0ac5674c461f26e90f425))


## v1.5.0 (2026-04-12)

### Features

- **ui**: Add header icon, apple-touch-icon, favicon, PWA meta tags
  ([`4abdd69`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/4abdd69c35616e728e41e4ebedf941c3e08b110d))


## v1.4.0 (2026-04-12)

### Features

- **ui**: Add argon2 password auth, active/delivered split, datetime formatting, version label
  ([`a6f2384`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/a6f2384448d384c2b37455fa5c1518f0d7af583c))


## v1.3.0 (2026-04-12)

### Documentation

- Fix DPD auth type to manual_token, update API reference
  ([`5b058c5`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/5b058c567e379c388941c9074abb857e5a29847a))

### Features

- **ui**: Add Jinja2 + htmx dashboard with package timeline and accounts page
  ([`143e3fc`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/143e3fcc68f832895a6a31baeb026f8e5b271447))


## v1.2.0 (2026-04-12)

### Features

- **dpd**: Implement DPD carrier with Keycloak auth and HTML scraping
  ([`4e8b56f`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/4e8b56f6f645e218441c2fb8b452da8c14bba5cc))


## v1.1.1 (2026-04-11)

### Bug Fixes

- **dhl**: Use single-session login+fetch for cookie-based auth
  ([`2c44360`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/2c4436027c48e25d6e9d5b12d1f69c97b6fcd3d7))


## v1.1.0 (2026-04-11)

### Features

- **dhl**: Implement DHL eCommerce NL carrier with credentials login and parcel API
  ([`e282693`](https://github.com/stevendejongnl/dude-wheres-my-package/commit/e28269360f490d73e42f16318bd40080e46d2cf9))


## v1.0.0 (2026-04-11)

- Initial Release
