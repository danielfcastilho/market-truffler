import { redirect } from "next/navigation";

/** Ranking studies now live with the models they evaluate. Browsers keep a
 * `#study_id` fragment across the redirect, and each study keeps its anchor. */
export default function RankingResearchPage() {
  redirect("/oink-corp/models");
}
