/* ============================================================
   CORTEX ACCOUNT PANEL — JavaScript
   Sidebar toggle, tab switching, interactions
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {
    // ---- Topbar User Dropdown ----
    const topbarUser = document.getElementById('topbarUserMenu');
    const topbarDropdown = document.getElementById('topbarDropdown');

    if (topbarUser && topbarDropdown) {
        topbarUser.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = topbarDropdown.classList.toggle('show');
            topbarUser.classList.toggle('open', isOpen);
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!topbarDropdown.contains(e.target) && !topbarUser.contains(e.target)) {
                topbarDropdown.classList.remove('show');
                topbarUser.classList.remove('open');
            }
        });

        // Close dropdown on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                topbarDropdown.classList.remove('show');
                topbarUser.classList.remove('open');
            }
        });
    }

    // ---- Sidebar Toggle (Mobile) ----
    const toggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('accountSidebar');
    const overlay = document.getElementById('sidebarOverlay');

    if (toggle && sidebar) {
        toggle.addEventListener('click', () => {
            sidebar.classList.toggle('open');
        });

        if (overlay) {
            overlay.addEventListener('click', () => {
                sidebar.classList.remove('open');
            });
        }

        // Close sidebar on nav item click (mobile)
        sidebar.querySelectorAll('.account-nav-item').forEach(item => {
            item.addEventListener('click', () => {
                if (window.innerWidth <= 1024) {
                    sidebar.classList.remove('open');
                }
            });
        });
    }

    // ---- Tab Switching (Usage page) ----
    document.querySelectorAll('.account-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            tab.closest('.account-tabs').querySelectorAll('.account-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
        });
    });

    // ---- Copy Email Button ----
    document.querySelectorAll('[data-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
            const text = btn.getAttribute('data-copy');
            navigator.clipboard.writeText(text).then(() => {
                const original = btn.textContent;
                btn.textContent = '✓';
                setTimeout(() => { btn.textContent = original; }, 1500);
            });
        });
    });
});
