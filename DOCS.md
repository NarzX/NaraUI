# 📖 Dokumentasi Resmi NaraUI
*Versi: NaraUI v1.0.0-pro | Auto-generated dari compiler.*

## 🚀 Cara Pakai (CLI)
```bash
python nara.py app.nui             # Compile sekali jadi app.html
python nara.py app.nui --watch     # Dev server + hot reload (localhost:8080)
python nara.py app.nui --build     # Minify HTML untuk produksi
python nara.py app.nui --build --pwa --embed  # PWA (offline) + Web Component
python nara.py app.nui --strict    # Compile + Linter strict (gagal jika ada warning)
python nara.py --docs              # Generate file DOCS.md ini
python nara.py --playground        # Buka editor web interaktif
```

## 🧠 Core Concepts
### 1. State & Reaktivitas
- `state: nama = nilai;` -> Variabel reaktif (auto update UI).
- `@persist state: nama = nilai;` -> Disimpan otomatis di LocalStorage (anti hilang saat refresh).
- `computed: total = harga * qty;` -> Dihitung otomatis saat dependensinya berubah.
- `resource: users = fetch('url');` -> Otomatis bikin `users`, `users_loading`, `users_error`.

### 2. Binding & Event
- `bind: nama_state` -> Two-way binding untuk Input, Select, Toggle, Checkbox, Slider.
- `on-click: { state += 1 }` -> Eksekusi kode saat diklik.
- `on-swipe-left/right: ...` -> Gesture swipe untuk layar sentuh.
- `emit('nama_event')` -> Kirim event dari Component child ke parent.
- `on-nama_event: ...` -> Tangkap event dari child component.

### 3. Styling & Tema (Tanpa CSS External)
- **CSS langsung:** `size: 16px; color: #fff; weight: bold;`
- **Dark Mode Pipe:** `color: black | dark: white;`
- **Responsive Pipe:** `size: 14px | md: 18px | lg: 24px;` (sm:640, md:768, lg:1024, xl:1280)
- **Hover Effect:** `hover-scale: 1.05; hover-shadow: ...; hover-bg: ...;`
- **Animasi:** `animate: fade-in | fade-in-up | pop-out;`

## 🧱 Tag Bawaan (Built-in)
### Layout
- **`App`**: Root aplikasi. `App "Judul" { ... }`
- **`Container`**: Wrapper konten utama (max-width 1200px, padding).
- **`Row`**: Flexbox horizontal (`gap`, `justify-content`, `align-items`).
- **`Column`**: Flexbox vertikal.
- **`Card`**: Kotak dengan border, shadow, dan padding.

### Teks & Media
- **`Text`**: Teks. Dukungan binding: `Text "Halo {nama}" {}`
- **`Image`**: Gambar. `Image "url.png" { width: 100px; }`
- **`Icon`**: Font Awesome icon. `Icon "fas fa-home" { color: red; }`

### Form
- **`Input`**: Input teks. `Input "Placeholder" { bind: state; }`
- **`TextArea`**: Input teks multiline.
- **`Select`**: Dropdown. Berisi `Option "Label" {}`
- **`Toggle`**: Switch on/off. `Toggle "Mode Gelap" { bind: dark_mode; }`
- **`Checkbox`**: Kotak centang.
- **`Slider`**: Range slider. `Slider "Volume" { bind: vol; min: 0; max: 100; }`

### Interaksi & Logika
- **`Button`**: Tombol. `Button "Klik" { on-click: n += 1; }`
- **`If / Else`**: Kondisi. `If "n > 0" { ... } Else { ... }`
- **`For`**: Looping. `For "item in list" { ... }`
- **`Route`**: Halaman SPA. `Route "/about" { transition: slide; ... }`
- **`Component`**: Deklarasi komponen: `Component Nama(arg1) { ... }`
- **`Slot`**: Tempat menaruh child di dalam Component.

## 🛠️ Ekosistem Bawaan
- **NaraFS**: IndexedDB wrapper. `await NaraFS.write('file.txt', 'halo')` dan `await NaraFS.read('file.txt')`.
- **toast("pesan")**: Munculkan notifikasi pop-up dari bawah.
- **DevTools**: Tambahkan `?nara-debug` di URL browser untuk melihat panel state & render time secara live.

---
*Made with 🥰 by NarzX (github.com/nezXproject)*