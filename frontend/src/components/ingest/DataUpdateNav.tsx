import { useNavigate } from "react-router-dom";
import SoftSegment from "../ui/SoftSegment";

export type DataUpdateTopMode = "gl" | "account-mapping";
export type ChartOfAccountsView = "upload" | "editor";

export default function DataUpdateNav({
  topMode,
  accountView,
}: {
  topMode: DataUpdateTopMode;
  accountView?: ChartOfAccountsView;
}) {
  const navigate = useNavigate();

  return (
    <>
      <div className="mb-5">
        <h1 className="text-2xl font-semibold text-slate-900">Data Update</h1>
        <p className="mt-1 text-sm text-slate-500 max-w-2xl">
          Upload new accounting files or update your chart of accounts.
        </p>
      </div>

      <div className="mb-4">
        <SoftSegment
          value={topMode}
          onChange={(v) => {
            if (v === "gl") navigate("/ingestion");
            else navigate("/ingestion?mode=account-mapping");
          }}
          options={[
            { value: "gl", label: "Accounting data" },
            { value: "account-mapping", label: "Chart of accounts" },
          ]}
        />
      </div>

      {topMode === "account-mapping" && (
        <div className="mb-6">
          <SoftSegment
            value={accountView ?? "upload"}
            onChange={(v) => {
              if (v === "upload") navigate("/ingestion?mode=account-mapping");
              else navigate("/mapping-editor");
            }}
            options={[
              { value: "upload", label: "Upload" },
              { value: "editor", label: "Editor" },
            ]}
          />
        </div>
      )}
    </>
  );
}
