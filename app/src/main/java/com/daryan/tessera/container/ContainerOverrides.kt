// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Where the user chose to run an app, by app id: the name of a Droidspaces container on the
 * phone, for an app that runs there instead of in the gramarye env. Kept beside the manifest
 * like [ExecOverrides], so a rescan of the system's menu keeps the choice and dropping it is the
 * reset. An app with no entry runs wherever the env sends its sessions.
 */
class ContainerOverrides(private val file: File) {
    private val _overrides = MutableStateFlow(load())
    val overrides: StateFlow<Map<String, String>> = _overrides

    /** [app] with the user's container, if they chose one. */
    fun apply(app: PermissionManifest): PermissionManifest =
        _overrides.value[app.id]?.let { app.copy(container = it) } ?: app

    /**
     * Has [app] run in [container], or in the env again when that is null. Returns what's wrong
     * with the name instead, leaving things as they were, when it isn't a container's name.
     */
    fun set(app: PermissionManifest, container: String?): List<String> {
        if (container != null && !PermissionManifest.CONTAINER.matches(container)) {
            return listOf("container '$container' is not a container's name")
        }
        edit { if (container == null) it - app.id else it + (app.id to container) }
        return emptyList()
    }

    fun reset(appId: String) = edit { it - appId }

    private fun edit(f: (Map<String, String>) -> Map<String, String>) {
        _overrides.update(f)
        runCatching {
            file.parentFile?.mkdirs()
            val tmp = File(file.path + ".tmp")
            tmp.writeText(json.encodeToString(SERIALIZER, _overrides.value))
            // Some filesystems won't rename over an existing file.
            if (!tmp.renameTo(file)) {
                file.delete()
                tmp.renameTo(file)
            }
        }
    }

    // A name that is none (a file edited by hand) is dropped: it would only fail every launch.
    private fun load(): Map<String, String> =
        runCatching { json.decodeFromString(SERIALIZER, file.readText()) }.getOrDefault(emptyMap())
            .filterValues { PermissionManifest.CONTAINER.matches(it) }

    private companion object {
        val json = Json { ignoreUnknownKeys = true; prettyPrint = true }
        val SERIALIZER = MapSerializer(String.serializer(), String.serializer())
    }
}
