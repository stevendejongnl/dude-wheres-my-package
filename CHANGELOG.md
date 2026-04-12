# CHANGELOG

<!-- version list -->

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
