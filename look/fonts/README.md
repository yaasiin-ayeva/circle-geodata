# Fonts for the baked atlases

`build_lookdev.py` draws the shop signs and the billboards with these, so the atlases come out the same on Linux, macOS
and Windows. Before, the script read them from `/usr/share/fonts/truetype/dejavu/`, which only Debian-like systems
have (and Ubuntu's build differs from upstream's).

- `DejaVuSans-Bold.ttf`, `DejaVuSansCondensed-Bold.ttf`: DejaVu Fonts 2.37, unmodified, from
  <https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37> (`dejavu-fonts-ttf-2.37.tar.bz2`).
- Licence: the Bitstream Vera and Arev fonts licences, with DejaVu's changes in the public domain
  (`LICENSE-DejaVu.txt`). They allow use, embedding and redistribution, also commercially; the fonts may not be sold on
  their own.
- `SHA256SUMS`: checked at each build.
