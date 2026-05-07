#include <windows.h>
#include <SimConnect.h>

#include <cctype>
#include <iostream>
#include <string>

static HANDLE hSimConnect = nullptr;

enum RequestId {
    REQ_MCDU = 5000
};

enum DefinitionId {
    DEF_MCDU_RAW = 6000
};

enum ClientDataId {
    CLIENT_MCDU = 7000
};

struct ProbeConfig {
    std::string clientDataName;
    int width = 24;
    int height = 14;
    int cellSizeBytes = 2;
    bool transposed = false;
};

static ProbeConfig config;

static char printable(unsigned char c) {
    if (c >= 32 && c <= 126) return static_cast<char>(c);
    return ' ';
}

static void printAsGrid(const unsigned char* raw, int width, int height, int cellSizeBytes) {
    std::cout << "\n========== iniBuilds MCDU probe ==========" << std::endl;
    std::cout << "name=" << config.clientDataName
              << " cellSizeBytes=" << cellSizeBytes
              << " grid=" << width << "x" << height
              << " transposed=" << (config.transposed ? "true" : "false")
              << std::endl;

    for (int row = 0; row < height; ++row) {
        std::string line;

        for (int col = 0; col < width; ++col) {
            int idx;
            if (config.transposed) {
                idx = ((col * height) + row) * cellSizeBytes;
            } else {
                idx = ((row * width) + col) * cellSizeBytes;
            }

            unsigned char ch = raw[idx];
            line.push_back(printable(ch));
        }

        std::cout << line << std::endl;
    }

    std::cout << "==========================================" << std::endl;
}

static void CALLBACK dispatchProc(SIMCONNECT_RECV* pData, DWORD cbData, void* pContext) {
    switch (pData->dwID) {
        case SIMCONNECT_RECV_ID_OPEN:
            std::cout << "Connected to SimConnect." << std::endl;
            break;

        case SIMCONNECT_RECV_ID_QUIT:
            std::cout << "MSFS quit." << std::endl;
            hSimConnect = nullptr;
            break;

        case SIMCONNECT_RECV_ID_EXCEPTION: {
            auto* ex = reinterpret_cast<SIMCONNECT_RECV_EXCEPTION*>(pData);
            std::cerr << "SimConnect exception: " << ex->dwException
                      << " sendID=" << ex->dwSendID
                      << " index=" << ex->dwIndex << std::endl;
            break;
        }

        case SIMCONNECT_RECV_ID_CLIENT_DATA: {
            auto* cd = reinterpret_cast<SIMCONNECT_RECV_CLIENT_DATA*>(pData);
            const unsigned char* raw = reinterpret_cast<const unsigned char*>(&cd->dwData);
            printAsGrid(raw, config.width, config.height, config.cellSizeBytes);
            break;
        }

        default:
            break;
    }
}

static bool subscribeRaw() {
    HRESULT hr;

    hr = SimConnect_MapClientDataNameToID(
        hSimConnect,
        config.clientDataName.c_str(),
        CLIENT_MCDU
    );

    if (FAILED(hr)) {
        std::cerr << "MapClientDataNameToID failed." << std::endl;
        return false;
    }

    const DWORD bytesToRead = config.width * config.height * config.cellSizeBytes;

    hr = SimConnect_AddToClientDataDefinition(
        hSimConnect,
        DEF_MCDU_RAW,
        0,
        bytesToRead,
        0,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "AddToClientDataDefinition failed." << std::endl;
        return false;
    }

    hr = SimConnect_RequestClientData(
        hSimConnect,
        CLIENT_MCDU,
        REQ_MCDU,
        DEF_MCDU_RAW,
        SIMCONNECT_CLIENT_DATA_PERIOD_SECOND,
        SIMCONNECT_CLIENT_DATA_REQUEST_FLAG_CHANGED,
        0,
        0,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "RequestClientData failed." << std::endl;
        return false;
    }

    std::cout << "Subscribed to candidate client data area: "
              << config.clientDataName << std::endl;

    return true;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr
            << "Usage:\n"
            << "  inibuilds_mcdu_probe.exe <ClientDataName> [cellSizeBytes] [transposed]\n\n"
            << "Examples:\n"
            << "  inibuilds_mcdu_probe.exe INIBUILDS_A340_MCDU_0 2\n"
            << "  inibuilds_mcdu_probe.exe INIBUILDS_A340_MCDU_0 4 true\n";
        return 1;
    }

    config.clientDataName = argv[1];

    if (argc >= 3) {
        config.cellSizeBytes = std::stoi(argv[2]);
    }

    if (argc >= 4) {
        std::string t = argv[3];
        config.transposed = (t == "true" || t == "1" || t == "yes");
    }

    HRESULT hr = SimConnect_Open(
        &hSimConnect,
        "iniBuilds MCDU Probe",
        nullptr,
        0,
        nullptr,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "Could not connect to SimConnect. Is MSFS running?" << std::endl;
        return 1;
    }

    if (!subscribeRaw()) {
        SimConnect_Close(hSimConnect);
        return 1;
    }

    std::cout << "Listening. Enable External Hardware / MCDU Export in the iniBuilds EFB if available." << std::endl;

    while (hSimConnect != nullptr) {
        SimConnect_CallDispatch(hSimConnect, dispatchProc, nullptr);
        Sleep(50);
    }

    SimConnect_Close(hSimConnect);
    return 0;
}
