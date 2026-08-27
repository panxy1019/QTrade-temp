import type { IntradayFactorDiscovery } from '../types'
import { canonicalJson } from './strategyIdentity'

const BLOCK_IDS = [1, 2, 3, 4, 5, 6, 7, 8]
const RANK_COMPONENTS = [
  'GATE_PASSED',
  'NEWEY_WEST_T_STATISTIC',
  'SESSION_CONDITIONAL_EXCESS',
  'LOWER_BODY_THRESHOLD',
]

export function matchesIntradayDiscoveryOverfitting(
  audit: IntradayFactorDiscovery['overfitting_audit'],
  selectedDays: readonly string[],
  candidateIds: readonly string[],
): boolean {
  if (selectedDays.length !== 40 || candidateIds.length !== 104) return false
  const candidateIdSet = new Set(candidateIds)
  const expectedBlocks = partitionDays(selectedDays)
  const expectedTrainingBlocks = combinations(BLOCK_IDS, 4)
  const splitIds = audit.splits.map((row) => row.split_id)
  const selections = new Map<string, number>()

  const blocksMatch = audit.blocks.length === 8
    && audit.blocks.every((block, index) => (
      block.block_id === index + 1
      && block.start === expectedBlocks[index][0]
      && block.end === expectedBlocks[index][expectedBlocks[index].length - 1]
      && block.session_count === expectedBlocks[index].length
    ))
  const splitsMatch = audit.splits.length === expectedTrainingBlocks.length
    && new Set(splitIds).size === audit.splits.length
    && audit.splits.every((row, index) => {
      const expectedTraining = expectedTrainingBlocks[index]
      const expectedTest = BLOCK_IDS.filter((blockId) => !expectedTraining.includes(blockId))
      const averageRank = Number(row.test_average_rank)
      const percentile = Number(row.test_rank_percentile)
      const logit = Number(row.test_rank_logit)
      if (!candidateIdSet.has(row.selected_candidate_id)) return false
      selections.set(row.selected_candidate_id, (selections.get(row.selected_candidate_id) ?? 0) + 1)
      return row.split_id === `CSCV-${String(index + 1).padStart(3, '0')}`
        && sameNumbers(row.training_block_ids, expectedTraining)
        && sameNumbers(row.test_block_ids, expectedTest)
        && finiteDecimal(row.training_excess_return)
        && finiteDecimal(row.test_excess_return)
        && Number.isFinite(averageRank)
        && averageRank >= 1
        && averageRank <= candidateIds.length
        && percentile > 0
        && percentile < 1
        && approximatelyEqual(percentile, (averageRank - 0.5) / candidateIds.length)
        && Number.isFinite(logit)
        && approximatelyEqual(logit, Math.log(percentile / (1 - percentile)))
        && row.below_median === (logit <= 0)
    })
  if (!blocksMatch || !splitsMatch || selections.size === 0) return false

  const testExcessReturns = audit.splits
    .map((row) => Number(row.test_excess_return))
    .sort((left, right) => left - right)
  const middle = testExcessReturns.length / 2
  const median = (testExcessReturns[middle - 1] + testExcessReturns[middle]) / 2
  const belowMedianCount = audit.splits.filter((row) => row.below_median).length
  const positiveCount = audit.splits.filter((row) => Number(row.test_excess_return) > 0).length
  const orderedSelections = [...selections.entries()].sort((left, right) => (
    right[1] - left[1] || left[0].localeCompare(right[0])
  ))
  const [mostSelectedId, mostSelectedCount] = orderedSelections[0]
  const probability = belowMedianCount / audit.splits.length
  const positiveRatio = positiveCount / audit.splits.length
  const criteria = {
    probability_of_backtest_overfitting_below_0_25: probability < 0.25,
    selected_oos_positive_excess_ratio_at_least_0_60: positiveRatio >= 0.60,
    median_selected_oos_excess_return_positive: median > 0,
  }

  return audit.method === 'COMBINATORIALLY_SYMMETRIC_CROSS_VALIDATION'
    && audit.block_count === 8
    && audit.split_count === expectedTrainingBlocks.length
    && audit.selection_metric === 'TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_SESSION_CONDITIONAL_EXCESS'
    && audit.rank_tie_method === 'AVERAGE_RANK'
    && sameStrings(audit.rank_components, RANK_COMPONENTS)
    && audit.block_partition === 'EIGHT_CONTIGUOUS_BALANCED_BLOCKS_OLDEST_TO_NEWEST'
    && audit.training_blocks_per_split === 4
    && audit.test_blocks_per_split === 4
    && audit.reuses_original_locked_test_blocks === true
    && audit.candidate_selection_uses_only_each_cscv_training_partition === true
    && audit.changes_original_locked_candidate === false
    && audit.independent_confirmation === false
    && audit.promotion_authority === false
    && audit.below_median_split_count === belowMedianCount
    && approximatelyEqual(Number(audit.probability_of_backtest_overfitting), probability)
    && audit.selected_oos_positive_excess_split_count === positiveCount
    && approximatelyEqual(Number(audit.selected_oos_positive_excess_ratio), positiveRatio)
    && approximatelyEqual(Number(audit.median_selected_oos_excess_return), median)
    && audit.unique_selected_candidate_count === selections.size
    && audit.most_selected_candidate_id === mostSelectedId
    && audit.most_selected_candidate_count === mostSelectedCount
    && canonicalJson(audit.criteria) === canonicalJson(criteria)
    && audit.passed === Object.values(criteria).every(Boolean)
}

function partitionDays(selectedDays: readonly string[]): string[][] {
  const baseSize = Math.floor(selectedDays.length / BLOCK_IDS.length)
  const remainder = selectedDays.length % BLOCK_IDS.length
  let offset = 0
  return BLOCK_IDS.map((_, index) => {
    const size = baseSize + (index < remainder ? 1 : 0)
    const block = selectedDays.slice(offset, offset + size)
    offset += size
    return block
  })
}

function combinations(values: readonly number[], size: number): number[][] {
  const result: number[][] = []
  function visit(start: number, selected: number[]): void {
    if (selected.length === size) {
      result.push(selected)
      return
    }
    for (let index = start; index <= values.length - (size - selected.length); index += 1) {
      visit(index + 1, [...selected, values[index]])
    }
  }
  visit(0, [])
  return result
}

function finiteDecimal(value: string): boolean {
  return value.trim().length > 0 && Number.isFinite(Number(value))
}

function approximatelyEqual(left: number, right: number): boolean {
  if (!Number.isFinite(left) || !Number.isFinite(right)) return false
  return Math.abs(left - right) <= Math.max(1e-12, Math.abs(right) * 1e-9)
}

function sameNumbers(left: readonly number[], right: readonly number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
