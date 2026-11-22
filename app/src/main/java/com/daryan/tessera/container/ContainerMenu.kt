// Copyright (C) 2026 Daryan Kamaran Karim
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import com.daryan.tessera.Shortcut

/**
 * What a Droidspaces container actually has, for its own app menu: a container is the user's
 * system, not one Tessera installed, so bundled apps and Store entries made in another engine
 * are listed only where their files are. PRoot and chroot list every entry, offering Install.
 */
data class ContainerMenu(
    /** Of the entries' programs (absolute paths), those in the container; null until checked. */
    val programs: Set<String>?,
    /** dpkg's installed packages there; null until read. */
    val packages: Set<String>?,
    /** The Flathub apps whose files are there, by Flathub id; null until read. */
    val flatpaks: Set<String>?,
) {
    /** Whether [app] is in the container. Unknown counts as not, so nothing shows that isn't there. */
    fun has(app: PermissionManifest): Boolean {
        val exe = app.exec.firstOrNull()
        return when {
            app.flatpak != null -> flatpaks?.contains(app.flatpak.app) == true
            // A shortcut's command is bash's; what it needs beyond that is its packages.
            Shortcut.isShortcut(app) -> app.packages.isEmpty() || packages?.containsAll(app.packages) == true
            exe != null && exe.startsWith("/") && programs != null -> exe in programs
            app.packages.isNotEmpty() -> packages?.containsAll(app.packages) == true
            // Needs nothing installed (the terminals), or a command found on PATH.
            else -> true
        }
    }

    /** The programs worth checking for [apps]: what [has] looks up in [programs]. */
    companion object {
        fun programsOf(apps: List<PermissionManifest>): Set<String> = apps
            .filter { it.flatpak == null && !Shortcut.isShortcut(it) }
            .mapNotNull { a -> a.exec.firstOrNull()?.takeIf { it.startsWith("/") } }
            .toSet()
    }
}

/**
 * A system's own menu entries ([system], from its .desktop files), minus what the launcher
 * lists already ([known]): same name, or the same program (Firefox's desktop file next to the
 * bundled Firefox entry).
 */
fun newMenuApps(system: List<PermissionManifest>, known: List<PermissionManifest>): List<PermissionManifest> {
    fun program(m: PermissionManifest) = m.exec.firstOrNull()?.substringAfterLast('/')
    val names = known.map { it.name.lowercase() }.toSet()
    val programs = known.mapNotNull(::program).toSet() + known.flatMap { it.packages }
    return system.filterNot { it.name.lowercase() in names || program(it) in programs }
}

/**
 * What one of the Droidspaces containers beside a gramarye env has, as the env's agent let the
 * launcher read it: [menu] for the launcher's entries, [apps] from its own .desktop files.
 */
data class ContainerView(val menu: ContainerMenu, val apps: List<PermissionManifest>)

/**
 * Everything [view]'s container has, as its own menu would list it: the launcher's [entries]
 * whose files are there, then its own menu entries. What a desktop running there can start.
 */
fun appsOf(view: ContainerView, entries: List<PermissionManifest>): List<PermissionManifest> {
    val known = entries.filter(view.menu::has)
    return known + newMenuApps(view.apps, known)
}

/**
 * The apps only a container has, each set to run there: of the launcher's [entries], those
 * whose files are in a container and not in the env, then the container's own menu entries.
 * [listed] is what the env's menu shows; an app it has stays the env's, to be moved by hand.
 * An app in two containers goes to the first of [views].
 */
fun containerApps(
    entries: List<PermissionManifest>,
    listed: List<PermissionManifest>,
    views: Map<String, ContainerView>,
): List<PermissionManifest> {
    val found = mutableListOf<PermissionManifest>()
    views.forEach { (name, view) ->
        val have = listed + found
        val ids = have.map { it.id }.toSet()
        val known = entries.filter { it.id !in ids && view.menu.has(it) }
        val own = newMenuApps(view.apps, have + known).filter { it.id !in ids }
        found += (known + own).map { it.copy(container = name) }
    }
    return found
}
