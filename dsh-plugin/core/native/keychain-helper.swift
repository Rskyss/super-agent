import Foundation
import LocalAuthentication
import Security

/// 命令行参数解析结果（绝不从命令行参数接收敏感值，敏感值仅从标准输入 JSON 读取）。
private struct CliArgs {
    let action: String
    let service: String
    let account: String
}

private enum HelperFailure: Error {
    case invalidArguments
    case invalidService
    case invalidAccount
    case invalidInput
    case emptySecret
    case interactionNotAllowed
    case authFailed
    case duplicateItem
    case keychainStatus(OSStatus)
    case encodingError

    var code: String {
        switch self {
        case .invalidArguments: return "invalid_arguments"
        case .invalidService: return "invalid_service"
        case .invalidAccount: return "invalid_account"
        case .invalidInput: return "invalid_stdin_json"
        case .emptySecret: return "empty_secret"
        case .interactionNotAllowed: return "interaction_not_allowed"
        case .authFailed: return "auth_failed"
        case .duplicateItem: return "duplicate_item"
        case .keychainStatus(let status): return "os_status_\(status)"
        case .encodingError: return "encoding_error"
        }
    }
}

private func isValidService(_ service: String) -> Bool {
    guard service == "com.aisoup.superagent" || service.hasPrefix("com.aisoup.superagent.") else {
        return false
    }
    if service.count > 128 { return false }
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if service.unicodeScalars.contains(where: { !allowed.contains($0) }) {
        return false
    }
    if service.hasSuffix(".") || service.contains("..") {
        return false
    }
    return true
}

private func isValidAccount(_ account: String) -> Bool {
    guard !account.isEmpty, account.count <= 256 else { return false }
    for scalar in account.unicodeScalars {
        if scalar.value < 0x20 || scalar.value == 0x7F {
            return false
        }
    }
    return true
}

private func parseArgs(_ argv: [String]) throws -> CliArgs {
    // argv[0] 为可执行文件路径；至少需要 action、--service <s\>、--account <a\>
    guard argv.count == 6 else {
        throw HelperFailure.invalidArguments
    }
    let action = argv[1]
    guard action == "get" || action == "set" || action == "delete" else {
        throw HelperFailure.invalidArguments
    }

    var service: String?
    var account: String?
    var index = 2
    while index + 1 < argv.count {
        let flag = argv[index]
        let value = argv[index + 1]
        switch flag {
        case "--service":
            guard service == nil else { throw HelperFailure.invalidArguments }
            service = value
        case "--account":
            guard account == nil else { throw HelperFailure.invalidArguments }
            account = value
        default:
            // 拒绝任何未知参数（包括禁止任何形式的 --value 参数）
            throw HelperFailure.invalidArguments
        }
        index += 2
    }

    guard let svc = service, isValidService(svc) else {
        throw HelperFailure.invalidService
    }
    guard let acct = account, isValidAccount(acct) else {
        throw HelperFailure.invalidAccount
    }
    return CliArgs(action: action, service: svc, account: acct)
}

private func makeNonInteractiveContext() -> LAContext {
    let context = LAContext()
    // 明确禁止弹出系统交互授权弹窗，测试或无界面场景下拒绝时立刻返回失败而非挂起
    context.interactionNotAllowed = true
    return context
}

private func baseQuery(service: String, account: String, authContext: LAContext) -> [String: Any] {
    return [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: account,
        kSecAttrSynchronizable as String: kCFBooleanFalse as Any,
        kSecUseAuthenticationContext as String: authContext,
    ]
}

private func mapStatus(_ status: OSStatus) -> HelperFailure {
    if status == errSecInteractionNotAllowed {
        return .interactionNotAllowed
    }
    if status == errSecAuthFailed || status == -25293 {
        return .authFailed
    }
    if status == errSecDuplicateItem {
        return .duplicateItem
    }
    return .keychainStatus(status)
}

private func writeJson(_ object: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

private func readSecretFromStdin() throws -> Data {
    let raw = FileHandle.standardInput.readDataToEndOfFile()
    guard !raw.isEmpty,
          let parsed = try? JSONSerialization.jsonObject(with: raw, options: []),
          let dict = parsed as? [String: Any],
          let value = dict["value"] as? String else {
        throw HelperFailure.invalidInput
    }
    guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        throw HelperFailure.emptySecret
    }
    guard let valueData = value.data(using: .utf8) else {
        throw HelperFailure.encodingError
    }
    return valueData
}

private func runAction(_ args: CliArgs) throws {
    let authCtx = makeNonInteractiveContext()
    let query = baseQuery(service: args.service, account: args.account, authContext: authCtx)

    switch args.action {
    case "get":
        var getQuery = query
        getQuery[kSecReturnData as String] = kCFBooleanTrue
        getQuery[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(getQuery as CFDictionary, &item)
        if status == errSecItemNotFound {
            try writeJson(["found": false])
            return
        }
        guard status == errSecSuccess else {
            throw mapStatus(status)
        }
        guard let data = item as? Data, let secret = String(data: data, encoding: .utf8) else {
            throw HelperFailure.encodingError
        }
        try writeJson(["found": true, "value": secret])

    case "set":
        let valueData = try readSecretFromStdin()
        let attributesToUpdate: [String: Any] = [
            kSecValueData as String: valueData,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributesToUpdate as CFDictionary)
        if updateStatus == errSecSuccess {
            try writeJson(["ok": true])
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw mapStatus(updateStatus)
        }

        var addQuery = query
        addQuery[kSecValueData as String] = valueData
        addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw mapStatus(addStatus)
        }
        try writeJson(["ok": true])

    case "delete":
        let deleteStatus = SecItemDelete(query as CFDictionary)
        if deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound {
            try writeJson(["ok": true])
            return
        }
        throw mapStatus(deleteStatus)

    default:
        throw HelperFailure.invalidArguments
    }
}

do {
    let args = try parseArgs(CommandLine.arguments)
    try runAction(args)
    exit(0)
} catch let err as HelperFailure {
    // 仅输出通用错误标识，绝不包含任何密钥或整段系统原始返回
    FileHandle.standardError.write(Data("keychain_helper_error:\(err.code)\n".utf8))
    exit(1)
} catch {
    FileHandle.standardError.write(Data("keychain_helper_error:unexpected_failure\n".utf8))
    exit(1)
}
