import styles from './audience-workspace.module.css'

export function cx(...classNames: Array<string | false | null | undefined>): string {
  return classNames
    .filter((className): className is string => Boolean(className))
    .map((className) => styles[className])
    .join(' ')
}
