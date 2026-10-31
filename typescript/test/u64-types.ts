import { fuzz, fuzzObserved, fuzzGuided, fuzzGuidedObserved, runCampaign, runGuidedCampaign, shrink,
    type FuzzConfig, type ShrinkConfig, type GenerateModel, type Feedback, type CampaignModel,
    type GuidanceConfig, type CampaignConfig, type Failure } from '../src/index.js';
import { runNodeCampaign, runNodeGuidedCampaign, type NodeCampaignWorker } from '../src/node_campaign.js';
declare const model: GenerateModel<number, number, number>;
declare const factory: () => CampaignModel<number, number, number>;
declare const feedback: Feedback<number, number, number, number>;
declare const optional: FuzzConfig | undefined;
declare const optionalShrink: ShrinkConfig | undefined;
declare const failure: Failure<number>;
declare const guidance: GuidanceConfig;
declare const config: FuzzConfig;
declare const campaign: CampaignConfig;
declare const worker: NodeCampaignWorker<number, number, unknown>;
const numberCases: number = fuzz<number, number, number>(model, optional).cases;
const numberAttempts: number = shrink<number, number, number>(model, failure, optionalShrink).attempts;
fuzzObserved<number, number, number>(model, config, {});
fuzzGuided<number, number, number, number>(model, config, guidance, feedback);
fuzzGuidedObserved<number, number, number, number>(model, config, guidance, feedback, {});
runCampaign<number, number, number>(campaign, {}, factory, () => {});
runGuidedCampaign<number, number, number, number>(campaign, {}, guidance, factory, () => feedback, () => {});
runNodeCampaign<number, number, unknown>(campaign, {}, worker, () => {});
runNodeGuidedCampaign<number, number, unknown>(campaign, {}, guidance, worker, () => {});
const exact: FuzzConfig<bigint> = { ...config, cases: 18446744073709551615n, maxSteps: 18446744073709551615n };
const exactCases: bigint = fuzz(model, exact).cases;
const exactAttempts: bigint = shrink(model, failure, { maxAttempts: 18446744073709551615n }).attempts;
// @ts-expect-error A bigint return requires an explicit exact config.
fuzz<number, number, number, bigint>(model);
// @ts-expect-error A bigint return requires an explicit exact config.
shrink<number, number, number, bigint>(model, failure);
void [numberCases, numberAttempts, exactCases, exactAttempts];

const literalConfig = { seed: 0n, cases: 1n, maxSteps: 1n, maxTransitions: 1n, mutationPercent: 0 } as const;
const literalReport = fuzz(model, literalConfig);
const widened: bigint = literalReport.cases;
// @ts-expect-error Counts are representation types, not the configured literal budget.
const literalCount: 1n = literalReport.cases;
void widened;
