// Copyright (C) 2026 Chris Anderson
// SPDX-License-Identifier: GPL-3.0-or-later

package com.daryan.tessera.container

import android.content.Context
import android.content.pm.ApplicationInfo
import android.net.Uri
import com.daryan.tessera.BackendMode

/**
 * Pairing with a gramarye env without a code typed, on a phone whose own system hands this app one.
 *
 * A code is what root asks the env's agent for (`tessera-pair`), which a user of a new phone has no
 * easy way to do. Where the system image ships this app, its driver app (which made the env and
 * can run things in it as root) asks in the user's place: it answers [METHOD] on [AUTHORITY] with
 * a fresh code, after checking for itself that the caller is the app its image ships. The code is
 * then used as a typed one is ([AohpPairing.pair]): the env's token is still made on the phone
 * and never leaves it.
 */
object AohpAutoPair {
    const val AUTHORITY = "org.aohp.driver.pairing"
    const val METHOD = "tessera-pair"
    private const val KEY_CODE = "code"

    /**
     * Whether to ask for a code now. Never once the user has unpaired ([off]) or chosen another
     * backend; otherwise when there is no env yet, or the env no longer takes this app's token
     * ([refused], asked only then: it was made anew).
     */
    fun wanted(off: Boolean, chosen: BackendMode?, paired: Boolean, refused: () -> Boolean): Boolean = when {
        off -> false
        chosen == null -> !paired
        chosen == BackendMode.AOHP -> !paired || refused()
        else -> false
    }

    /** A fresh pairing code from the phone's system, or null when it gives none. */
    fun code(context: Context): String? {
        // Only a part of the system image is believed: any app could claim the name where there is none.
        val provider = context.packageManager.resolveContentProvider(AUTHORITY, 0) ?: return null
        if (provider.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM == 0) return null
        val answer = runCatching {
            context.contentResolver.call(Uri.parse("content://$AUTHORITY"), METHOD, null, null)
        }.getOrNull() ?: return null
        return answer.getString(KEY_CODE)?.let(AohpPairing::code)
    }
}
