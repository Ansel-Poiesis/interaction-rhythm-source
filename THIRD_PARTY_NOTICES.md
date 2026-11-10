# Third-party notices

The project MIT license does not replace the licenses listed here. The source release contains no third-party executable binary.

## LXGW WenKai Screen webfont

- Upstream package: [lxgw-wenkai-screen-webfont 1.7.0](https://www.npmjs.com/package/lxgw-wenkai-screen-webfont)
- Font files: SIL Open Font License 1.1; complete text is in static/fonts/lxgw-wenkai-screen/OFL.txt and LICENSE.
- CSS/package code: MIT; upstream notice is in static/fonts/lxgw-wenkai-screen/LICENSE.

## LXGW WenKai webfont

- Upstream package: [lxgw-wenkai-webfont 1.7.0](https://www.npmjs.com/package/lxgw-wenkai-webfont)
- Font files: SIL Open Font License 1.1; complete text is in static/fonts/lxgw-wenkai/OFL.txt and LICENSE.
- The font files are subsets. They remain under the font's OFL license and are not relicensed under the repository MIT license.

## Optional Tencent QQMail Agently CLI

The app can optionally use @tencent-qqmail/agently-cli 1.0.18 and its @tencent-qqmail/agently-cli-win32-x64 1.0.18 package for QQ Agent Mail delivery. These packages are not included in this source release. The optional preparation script downloads them from npm and places the executable in ignored local build output; it does not run during tests or normal source installation.

- License declared by npm metadata: Apache-2.0.
- Upstream wrapper: [@tencent-qqmail/agently-cli](https://www.npmjs.com/package/@tencent-qqmail/agently-cli)
- Upstream Windows executable package: [@tencent-qqmail/agently-cli-win32-x64](https://www.npmjs.com/package/@tencent-qqmail/agently-cli-win32-x64)
- npm SHA-512 integrity, wrapper: sha512-z4Ps7+X6LWY5P8ZO1vU/sfbqLsaqDfeM4Kot1/UX98ZJuQ80HJMH9cVGghySZNgThiwRABhyh9TLDeG1fYAnFg==
- npm SHA-512 integrity, Windows x64 package: sha512-LNFFyWOm3lE53ux9jpxSdzhbjceUk0mIb2Q314y4mkWR0Q1lP2IFRPlDdwPqSmI0Y7MphuH6AwHQeAojzsfJmQ==
- The wrapper's Apache-2.0 license text is included in licenses/agently-cli/LICENSE. The preparation script copies that notice beside the downloaded executable.

## Report interpretation cards

Weekly-report context cards are project-authored guardrails. This source release does not redistribute paper abstracts, review text, or internal research notes in those cards.

## Python dependencies

Runtime and development dependencies are declared in requirements.txt and requirements-dev.txt. They are installed from package indexes and are not vendored in this repository. Each resolved package remains under its own license; consult the package metadata for the exact version installed in your environment.
