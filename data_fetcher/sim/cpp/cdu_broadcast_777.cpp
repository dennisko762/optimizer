#include <windows.h>
#include <iostream>
#include <string>
#include <array>

#include "SimConnect.h"
#include "PMDG_777X_SDK.h"

// ------------------------------------------------------------
// Adjust these names if your installed PMDG_777X_SDK.h uses
// slightly different struct names.
// Search your header for "CDU_Screen" if this does not compile.
// ------------------------------------------------------------

using CDU_SCREEN = PMDG_777X_CDU_Screen;

static HANDLE hSimConnect = nullptr;

enum DATA_REQUEST_ID {
    REQUEST_CDU_0 = 100,
    REQUEST_CDU_1 = 101,
    REQUEST_CDU_2 = 102,
};

enum CLIENT_DATA_DEFINE_ID {
    DEFINITION_CDU_0 = 200,
    DEFINITION_CDU_1 = 201,
    DEFINITION_CDU_2 = 202,
};

static char decodeCduChar(const auto& cell) {
    // PMDG CDU cell structs usually expose a character/symbol field.
    // The exact member name may differ. Check PMDG_777X_SDK.h.
    //
    // Common possibilities:
    //   cell.Symbol
    //   cell.symbol
    //   cell.Char
    //   cell.ch
    //
    // Change this line if your SDK uses another field name.
    return static_cast<char>(cell.Symbol);
}

static void printCduScreen(const CDU_SCREEN& screen, int cduIndex) {
    std::cout << "\n================ CDU " << cduIndex << " ================\n";

    // PMDG CDU is very likely stored as [column][row], not [row][column]
    for (int row = 0; row < 14; ++row) {
        std::string line;
        line.reserve(24);

        for (int col = 0; col < 24; ++col) {
            const auto& cell = screen.Cells[col][row];  // IMPORTANT: col first, row second
            char ch = decodeCduChar(cell);

            if (ch == '\0') ch = ' ';
            line.push_back(ch);
        }

        std::cout << line << "\n";
    }

    std::cout << "======================================\n";
}

static void CALLBACK dispatchProc(SIMCONNECT_RECV* pData, DWORD cbData, void* pContext) {
    switch (pData->dwID) {
        case SIMCONNECT_RECV_ID_OPEN: {
            std::cout << "Connected to MSFS SimConnect.\n";
            break;
        }

        case SIMCONNECT_RECV_ID_QUIT: {
            std::cout << "MSFS quit.\n";
            hSimConnect = nullptr;
            break;
        }

        case SIMCONNECT_RECV_ID_EXCEPTION: {
            auto* ex = reinterpret_cast<SIMCONNECT_RECV_EXCEPTION*>(pData);
            std::cerr << "SimConnect exception: " << ex->dwException << "\n";
            break;
        }

        case SIMCONNECT_RECV_ID_CLIENT_DATA: {
            auto* clientData = reinterpret_cast<SIMCONNECT_RECV_CLIENT_DATA*>(pData);

            const CDU_SCREEN* screen =
                reinterpret_cast<const CDU_SCREEN*>(&clientData->dwData);

            switch (clientData->dwRequestID) {
                case REQUEST_CDU_0:
                    printCduScreen(*screen, 0);
                    break;

                case REQUEST_CDU_1:
                    printCduScreen(*screen, 1);
                    break;

                case REQUEST_CDU_2:
                    printCduScreen(*screen, 2);
                    break;

                default:
                    break;
            }

            break;
        }

        default:
            break;
    }
}

static void subscribeToCdu(
    const char* clientDataName,
    SIMCONNECT_CLIENT_DATA_ID clientDataId,
    SIMCONNECT_CLIENT_DATA_DEFINITION_ID definitionId,
    SIMCONNECT_DATA_REQUEST_ID requestId
) {
    HRESULT hr;

    hr = SimConnect_MapClientDataNameToID(
        hSimConnect,
        clientDataName,
        clientDataId
    );

    if (FAILED(hr)) {
        std::cerr << "Failed to map client data name: " << clientDataName << "\n";
        return;
    }

    hr = SimConnect_AddToClientDataDefinition(
        hSimConnect,
        definitionId,
        0,
        sizeof(CDU_SCREEN),
        0,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "Failed to add client data definition: " << clientDataName << "\n";
        return;
    }

    hr = SimConnect_RequestClientData(
        hSimConnect,
        clientDataId,
        requestId,
        definitionId,
        SIMCONNECT_CLIENT_DATA_PERIOD_VISUAL_FRAME,
        SIMCONNECT_CLIENT_DATA_REQUEST_FLAG_CHANGED,
        0,
        0,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "Failed to request client data: " << clientDataName << "\n";
        return;
    }

    std::cout << "Subscribed to " << clientDataName << "\n";
}

int main() {
    HRESULT hr = SimConnect_Open(
        &hSimConnect,
        "PMDG 777 CDU Listener",
        nullptr,
        0,
        nullptr,
        0
    );

    if (FAILED(hr)) {
        std::cerr << "Could not connect to SimConnect. Is MSFS running?\n";
        return 1;
    }

    std::cout << "SimConnect opened.\n";

    subscribeToCdu(
        PMDG_777X_CDU_0_NAME,
        PMDG_777X_CDU_0_ID,
        DEFINITION_CDU_0,
        REQUEST_CDU_0
    );

    subscribeToCdu(
        PMDG_777X_CDU_1_NAME,
        PMDG_777X_CDU_1_ID,
        DEFINITION_CDU_1,
        REQUEST_CDU_1
    );

    subscribeToCdu(
        PMDG_777X_CDU_2_NAME,
        PMDG_777X_CDU_2_ID,
        DEFINITION_CDU_2,
        REQUEST_CDU_2
    );

    std::cout << "Listening for PMDG 777 CDU broadcast...\n";

    while (hSimConnect != nullptr) {
        SimConnect_CallDispatch(hSimConnect, dispatchProc, nullptr);
        Sleep(50);
    }

    if (hSimConnect) {
        SimConnect_Close(hSimConnect);
    }

    return 0;
}